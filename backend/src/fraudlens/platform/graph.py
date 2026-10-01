"""The network view: a wallet's profile and neighbours, mule rings, agent risk.

All of it is read from the scorer's live state, so it shows what the models saw
at the last transaction. Rings and agent scores cost a few milliseconds over
the whole state; they are computed on demand and kept for a few seconds.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..features import RECIPIENT_FEATURES
from ..models.agents import METRICS, agent_table, score_agents
from ..models.rings import MAX_DEVICE_WALLETS, find_rings
from .audit import WorkflowError
from .models import Agent, Case, Decision, FreezeRequest, Transaction, Wallet, WalletFlag
from .scoring import Scorer

CACHE_SECONDS = 5.0
MAX_NODES = 80
AGENT_REVIEW_Z = 3.0


def _num(value: float | None) -> float | None:
    return None if value is None or not math.isfinite(value) else round(float(value), 4)


def txn_dict(t: Transaction) -> dict:
    return {
        "txn_id": t.txn_id,
        "ts": t.ts,
        "type": t.type,
        "sender_id": t.sender_id,
        "receiver_id": t.receiver_id,
        "amount": t.amount,
        "channel": t.channel,
        "district": t.district,
        "status": t.status,
        "status_reason": t.status_reason,
        "source": t.source,
    }


class Graph:
    def __init__(self, scorer: Scorer) -> None:
        self.scorer = scorer
        self._cache: dict[str, tuple[float, object]] = {}

    def _cached(self, key: str, build: Callable[[], object]):
        hit = self._cache.get(key)
        if hit is not None and time.monotonic() - hit[0] < CACHE_SECONDS:
            return hit[1]
        with self.scorer.lock:
            value = build()
        self._cache[key] = (time.monotonic(), value)
        return value

    # ---------------------------------------------------------------- wallets

    def _node(self, wallet_id: str) -> dict:
        scorer = self.scorer
        return {
            "id": wallet_id,
            "kind": "wallet",
            "flagged": wallet_id in scorer.engine.flagged,
            "frozen": wallet_id in scorer.frozen,
            "mule_score": _num(scorer.mule_scores.get(wallet_id)),
        }

    def risk(self, wallet_id: str) -> dict | None:
        """The receiving-side profile of a wallet as of now, and its mule score."""
        scorer = self.scorer
        with scorer.lock:
            engine = scorer.engine
            state = engine.wallets.get(wallet_id)
            if state is None:
                return None
            features = engine.recipient_features(wallet_id, max(scorer.clock.now(), engine.last_ts))
            shared = sorted(
                {
                    other
                    for device in state.devices
                    if 1 < len(engine.device_wallets.get(device, ())) <= MAX_DEVICE_WALLETS
                    for other in engine.device_wallets[device]
                }
                - {wallet_id}
            )
            flagged_at = engine.flagged.get(wallet_id)
            summary = {
                "transactions_initiated": state.n_init,
                "recipients": len(state.recipients),
                "senders": len(state.senders),
                "received_count": state.n_in,
                "received_total": round(state.sum_in, 2),
                "cashed_out_total": round(state.sum_cashout, 2),
                "handsets": len(state.devices),
                "agents_used": len(state.agents_used),
                "confirmed_fraud_neighbours": state.flagged_neighbors,
            }
        mule = threshold = None
        if scorer.bundle is not None:
            mule = _num(float(scorer.bundle.recipient_risk(features)[0]))
            threshold = scorer.decision.mule_threshold
        return {
            "confirmed_fraud": flagged_at is not None,
            "mule_score": mule,
            "mule_threshold": threshold,
            "mule_alert": mule is not None and threshold is not None and mule >= threshold,
            "highest_mule_score_seen": _num(scorer.mule_scores.get(wallet_id)),
            "features": dict(zip(RECIPIENT_FEATURES, map(_num, features), strict=True)),
            "summary": summary,
            "shares_handset_with": shared[:50],
        }

    def wallet(self, s: Session, wallet_id: str) -> dict:
        master = s.get(Wallet, wallet_id)
        risk = self.risk(wallet_id)
        if master is None and risk is None:
            raise WorkflowError(404, "wallet_not_found", f"no wallet {wallet_id}")
        flag = s.get(WalletFlag, wallet_id)
        involved = or_(Transaction.sender_id == wallet_id, Transaction.receiver_id == wallet_id)
        recent = s.scalars(
            select(Transaction).where(involved).order_by(Transaction.ts.desc()).limit(25)
        ).all()
        alerts = s.execute(
            select(Transaction, Decision)
            .join(Decision, Decision.txn_id == Transaction.txn_id)
            .where(involved, Decision.tier != "allow")
            .order_by(Decision.decided_at.desc())
            .limit(25)
        ).all()
        cases = s.scalars(
            select(Case).where(Case.subject_id == wallet_id).order_by(Case.opened_at.desc())
        ).all()
        freezes = s.scalars(
            select(FreezeRequest)
            .where(FreezeRequest.wallet_id == wallet_id)
            .order_by(FreezeRequest.created_at.desc())
        ).all()
        return {
            "wallet_id": wallet_id,
            "registered": master is not None,
            "created_at": master.created_at if master else None,
            "district": master.district if master else None,
            "area_type": master.area_type if master else None,
            "channel": master.channel if master else None,
            "segment": master.segment if master else None,
            "status": master.status if master else "active",
            "frozen_at": master.frozen_at if master else None,
            "flag": flag
            and {
                "flagged_at": flag.flagged_at,
                "reason": flag.reason,
                "source": flag.source,
                "case_id": flag.case_id,
            },
            "risk": risk,
            "recent_transactions": [txn_dict(t) for t in recent],
            "recent_alerts": [
                {**txn_dict(t), "tier": d.tier, "risk_score": d.risk_score, "case_id": d.case_id}
                for t, d in alerts
            ],
            "cases": [
                {
                    "id": c.id,
                    "status": c.status,
                    "priority": c.priority,
                    "source": c.source,
                    "opened_at": c.opened_at,
                    "verdict": c.verdict,
                }
                for c in cases
            ],
            "freeze_requests": [
                {
                    "id": f.id,
                    "status": f.status,
                    "reason": f.reason,
                    "requested_by": f.requested_by,
                    "decided_by": f.decided_by,
                    "created_at": f.created_at,
                }
                for f in freezes
            ],
        }

    def network(self, wallet_id: str, limit: int = 40) -> dict:
        """The wallet's direct neighbours: who pays it, whom it pays, where it cashes out,
        and which wallets have used the same handset."""
        limit = min(limit, MAX_NODES)
        scorer = self.scorer
        with scorer.lock:
            engine = scorer.engine
            state = engine.wallets.get(wallet_id)
            if state is None:
                raise WorkflowError(404, "wallet_not_found", f"no wallet {wallet_id}")
            nodes = {wallet_id: {**self._node(wallet_id), "role": "subject"}}
            edges: list[dict] = []
            truncated = False

            def add(kind: str, counts: dict[str, int], outward: bool) -> None:
                nonlocal truncated
                # Confirmed-fraud neighbours first, then the busiest links.
                ranked = sorted(counts, key=lambda k: (k not in engine.flagged, -counts[k]))
                truncated = truncated or len(ranked) > limit
                for other in ranked[:limit]:
                    if kind == "cash_out":
                        nodes.setdefault(other, {"id": other, "kind": "agent"})
                    else:
                        nodes.setdefault(other, self._node(other))
                    a, b = (wallet_id, other) if outward else (other, wallet_id)
                    edges.append({"source": a, "target": b, "kind": kind, "count": counts[other]})

            add("sent_to", state.recipients, True)
            add("received_from", state.senders, False)
            add("cash_out", state.agents_used, True)
            for device in state.devices:
                users = engine.device_wallets.get(device, ())
                if not 1 < len(users) <= MAX_DEVICE_WALLETS:
                    continue
                for other in sorted(users - {wallet_id})[:limit]:
                    nodes.setdefault(other, self._node(other))
                    edges.append(
                        {"source": wallet_id, "target": other, "kind": "shared_handset", "count": 1}
                    )
        return {
            "wallet_id": wallet_id,
            "nodes": list(nodes.values()),
            "edges": edges,
            "truncated": truncated,
        }

    # ------------------------------------------------------------------ rings

    def rings(self) -> list[dict]:
        def build() -> list[dict]:
            rings = find_rings(self.scorer.engine, dict(self.scorer.mule_scores))
            for ring in rings:
                # Named after its first member, so the id survives other rings appearing.
                ring["ring_id"] = f"R-{ring['wallets'][0]}"
                ring["frozen"] = sorted(w for w in ring["wallets"] if w in self.scorer.frozen)
            return rings

        return self._cached("rings", build)

    def ring(self, ring_id: str) -> dict:
        for ring in self.rings():
            if ring["ring_id"] == ring_id:
                return ring
        raise WorkflowError(404, "ring_not_found", f"no ring {ring_id}")

    # ----------------------------------------------------------------- agents

    def agents(self) -> list[dict]:
        def build() -> list[dict]:
            table = score_agents(agent_table(self.scorer.engine))
            rows = []
            for rank, row in enumerate(table.to_dict("records"), start=1):
                rows.append(
                    {
                        "rank": rank,
                        "agent_id": row["agent_id"],
                        "district": row["district"],
                        "risk": _num(row["risk"]),
                        "eligible": bool(row["eligible"]),
                        "review_suggested": bool(row["eligible"] and row["risk"] >= AGENT_REVIEW_Z),
                        "n_cashouts": int(row["n_cashouts"]),
                        "n_cashins": int(row["n_cashins"]),
                        "n_customers": int(row["n_customers"]),
                        "cashout_value": round(float(row["cashout_value"]), 2),
                        "metrics": {
                            m: {"value": _num(row[m]), "z": _num(row[f"z_{m}"])} for m in METRICS
                        },
                        "reasons": [
                            {"metric": m, "text": METRICS[m], "z": _num(row[f"z_{m}"])}
                            for m in row["reasons"]
                        ],
                    }
                )
            return rows

        return self._cached("agents", build)

    def agent(self, s: Session, agent_id: str) -> dict:
        row = next((a for a in self.agents() if a["agent_id"] == agent_id), None)
        if row is None:
            raise WorkflowError(404, "agent_not_found", f"no agent {agent_id}")
        with self.scorer.lock:
            engine = self.scorer.engine
            customers = engine.agents[agent_id].customers
            flagged = sorted(w for w in customers if w in engine.flagged)
            top = sorted(customers.items(), key=lambda kv: -kv[1])[:10]
        master = s.get(Agent, agent_id)
        recent = s.scalars(
            select(Transaction)
            .where(Transaction.receiver_id == agent_id, Transaction.type == "CASH_OUT")
            .order_by(Transaction.ts.desc())
            .limit(25)
        ).all()
        return {
            **row,
            "registered": master is not None,
            "confirmed_fraud_customers": flagged,
            "top_customers": [{"wallet_id": w, "transactions": n} for w, n in top],
            "recent_cash_outs": [txn_dict(t) for t in recent],
        }
