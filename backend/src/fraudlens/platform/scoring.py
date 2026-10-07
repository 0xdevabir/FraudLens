"""Online scoring: the one place that owns the feature state.

Features are behaviour counters held in memory (`FeatureEngine`), and a feature
vector is only right if every earlier transaction has been applied exactly once
and in order. So one `Scorer` per process owns the engine, and everything that
reads or changes it goes through `Scorer.lock`.

The database is the log. A transaction row carries `applied_at`, the domain time
at which it was applied to the engine, and `applied_seq`, its position among all
state changes (confirmed-fraud flags are numbered in the same sequence). After a
restart the scorer loads the snapshot taken at the end of the historical data and
re-applies every change since, in that order. Nothing else has to be saved.

Live transactions only change the state when they complete: an allowed one at
once, a warned or stepped-up one when the customer confirms, a held one when an
analyst releases it. Replayed transactions are recorded history, so they are
applied on arrival (the model was trained on features computed that way) while
the workflow still treats a hold as held.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

from redis import Redis
from sqlalchemy import func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..decision import Decision as EngineDecision
from ..decision import DecisionEngine, SimilarCases, context_from_engine, load_policy
from ..decision.policy import RANK
from ..features import SCORED_TYPES, FeatureEngine, Txn
from ..mlops.shadow import Shadow
from ..models import registry
from .audit import WorkflowError
from .events import FlagEvent, TxnEvent, moment
from .models import (
    Agent,
    Case,
    CaseEvent,
    Decision,
    ShadowScore,
    Transaction,
    Wallet,
    WalletFlag,
)

log = logging.getLogger(__name__)

BASELINE_SNAPSHOT = "engine_after_val.pkl"
# Events may arrive a little out of order; one this far behind the stream cannot be scored.
STALE_SECONDS = 300.0


class Clock:
    """Domain time: follows the event stream, and ticks in real time between events.

    In production events carry the current time and this is the wall clock. During
    a replay it is the recorded time of the latest event, so deadlines and case
    ages mean the same thing in both.
    """

    def __init__(self, start: float) -> None:
        self._latest = start
        self._offset = start - time.time()

    def observe(self, ts: float) -> None:
        if ts >= self._latest:
            self._latest = ts
            self._offset = ts - time.time()

    def now(self) -> float:
        return time.time() + self._offset


@dataclass
class Result:
    """What happened to one event. `status` is the transaction status, or
    'duplicate', 'stale' (not stored) or 'flagged' (a flag event)."""

    txn_id: int | None
    status: str
    scored: bool = False
    duplicate: bool = False
    status_reason: str | None = None
    decision: dict | None = None  # the summary returned to the caller
    alert: dict | None = None  # published to the live feed


_ALERT_DETAIL = (
    "rule_trace", "fallback_signals", "reasons", "customer_message",
    "recommended_actions", "similar_cases", "evidence", "segment",
)  # fmt: skip


def txn_from_row(row, ts: float | None = None) -> Txn:
    balance = row.sender_balance_before
    return Txn(
        row.txn_id,
        row.ts.timestamp() if ts is None else ts,
        row.type,
        row.sender_id,
        row.sender_type,
        row.receiver_id,
        row.receiver_type,
        float(row.amount),
        float("nan") if balance is None else float(balance),
        row.device_id,
        row.channel,
        row.district,
        row.network,
    )


def clean(value):
    """JSON for Postgres: no NaN or infinity (JSONB rejects them)."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


class Scorer:
    def __init__(
        self, settings: Settings, sessions: sessionmaker[Session], redis: Redis | None = None
    ) -> None:
        self.settings = settings
        self.sessions = sessions
        self.redis = redis
        self.lock = threading.RLock()
        self.policy = load_policy(settings.policy_version)
        self.bundle = None
        similar = None
        try:
            self.bundle = registry.load(settings.model_version, settings.models_dir)
            similar = SimilarCases.load(settings.models_dir / self.bundle.version)
        except FileNotFoundError as exc:
            if self.bundle is None:
                # Keep serving: the policy's rules-only fallback takes over, and says so.
                log.error("no model could be loaded (%s); serving in rules-only mode", exc)
            else:
                log.warning("no similar-case index for model %s", self.bundle.version)
        self.decision = DecisionEngine(self.policy, self.bundle, similar)
        self.shadow = self._load_shadow()
        self._shadow_rows: list[dict] = []
        self.engine = FeatureEngine()
        self.clock = Clock(0.0)
        self.frozen: set[str] = set()
        self.mule_scores: dict[str, float] = {}  # wallets the mule model has alerted on
        self.seq = 0  # last position written to the log
        self._new_wallets: list[dict] = []
        self._new_agents: list[dict] = []
        self.ready = False  # False while the in-memory state may not match the database
        self.recover()

    @property
    def mode(self) -> str:
        return "model" if self.bundle is not None else "rules_only"

    def _load_shadow(self) -> Shadow | None:
        """The challenger to record next to the served model, if one is configured."""
        version = self.settings.shadow_model_version
        if not version or self.bundle is None or version == self.bundle.version:
            return None
        try:
            return Shadow.load(version, self.settings.models_dir, self.policy)
        except Exception:
            # A challenger is optional: serving starts without it.
            log.exception("shadow model %s could not be loaded; shadow mode is off", version)
            return None

    def _shadow_score(self, txn_id: int, features) -> None:
        """Record what the challenger says. It can fail; the decision cannot depend on it."""
        t0 = time.perf_counter()
        try:
            risk, tier = self.shadow.score(features)
        except Exception:
            log.exception("shadow model failed on transaction %d; shadow mode is off", txn_id)
            self.shadow = None
            return
        self._shadow_rows.append(
            {
                "txn_id": txn_id,
                "model_version": self.shadow.version,
                "risk": risk,
                "tier": tier,
                "latency_ms": (time.perf_counter() - t0) * 1000,
            }
        )

    # ------------------------------------------------------------- recovery

    def recover(self) -> None:
        """Rebuild the in-memory state from the baseline snapshot and the database."""
        t0 = time.perf_counter()
        engine = FeatureEngine.load(self.settings.dataset_dir / BASELINE_SNAPSHOT)
        columns = (
            Transaction.txn_id, Transaction.ts, Transaction.type, Transaction.sender_id,
            Transaction.sender_type, Transaction.receiver_id, Transaction.receiver_type,
            Transaction.amount, Transaction.sender_balance_before, Transaction.device_id,
            Transaction.channel, Transaction.district, Transaction.network,
            Transaction.applied_at, Transaction.applied_seq,
        )  # fmt: skip
        applied = seq = 0
        with self.sessions() as s:
            # Parties first seen after the baseline were registered in the database.
            for w in s.execute(select(Wallet.wallet_id, Wallet.created_at, Wallet.district)):
                if w.wallet_id not in engine.wallets:
                    engine.register_wallet(w.wallet_id, w.created_at.timestamp(), w.district)
            for a in s.execute(select(Agent.agent_id, Agent.district)):
                engine.register_agent(a.agent_id, a.district)
            flags = s.execute(
                select(WalletFlag.wallet_id, WalletFlag.flagged_at, WalletFlag.applied_seq)
                .where(WalletFlag.applied_seq.is_not(None))
                .order_by(WalletFlag.applied_seq)
            ).all()
            j = 0
            rows = s.execute(
                select(*columns)
                .where(Transaction.applied_seq.is_not(None))
                .order_by(Transaction.applied_seq)
                .execution_options(yield_per=20_000)
            )
            for row in rows:
                while j < len(flags) and flags[j].applied_seq < row.applied_seq:
                    engine.flag_wallet(flags[j].wallet_id, flags[j].flagged_at.timestamp())
                    j += 1
                engine.update(txn_from_row(row, row.applied_at.timestamp()))
                applied, seq = applied + 1, row.applied_seq
            for flag in flags[j:]:
                engine.flag_wallet(flag.wallet_id, flag.flagged_at.timestamp())
            if flags:
                seq = max(seq, flags[-1].applied_seq)
            frozen = set(s.scalars(select(Wallet.wallet_id).where(Wallet.status == "frozen")))
            mule_scores: dict[str, float] = {}
            if self.decision.mule_threshold is not None:
                top = func.max(Decision.scores["mule"].as_float())
                mule_scores = dict(
                    s.execute(
                        select(Transaction.receiver_id, top)
                        .join(Decision, Decision.txn_id == Transaction.txn_id)
                        .group_by(Transaction.receiver_id)
                        .having(top >= self.decision.mule_threshold)
                    ).all()
                )
        with self.lock:
            self.engine, self.frozen, self.mule_scores = engine, frozen, mule_scores
            self.seq = seq
            self.ready = True
            self.clock = Clock(engine.last_ts)
        log.info(
            "scorer state rebuilt in %.1fs: %d transactions and %d flags applied since baseline",
            time.perf_counter() - t0,
            applied,
            len(flags),
        )

    # ------------------------------------------------------------ processing

    def process(self, events: list[TxnEvent | FlagEvent]) -> list[Result]:
        """Score and record a batch of events, in order, in one database transaction.

        Idempotent on `txn_id`: an event seen before returns the recorded outcome
        and changes nothing, so a caller or the stream may safely send it again.
        """
        with self.lock:
            if not self.ready:
                self.recover()
            try:
                with self.sessions() as s:
                    results = self._process(s, events)
                    s.commit()
            except Exception:
                # The engine may be ahead of what was committed. Rebuild it from the log;
                # if that fails too (database down), the next call tries again first.
                log.exception("batch failed; rebuilding scorer state from the database")
                self.ready = False
                self.recover()
                raise
        self._publish([r.alert for r in results if r.alert])
        return results

    def _process(self, s: Session, events: list[TxnEvent | FlagEvent]) -> list[Result]:
        ids = [e.txn.txn_id for e in events if isinstance(e, TxnEvent)]
        seen = set(s.scalars(select(Transaction.txn_id).where(Transaction.txn_id.in_(ids))))
        results: list[Result] = []
        txn_rows: list[dict] = []
        self._new_wallets, self._new_agents, self._shadow_rows = [], [], []
        decided: list[tuple[Result, Txn, EngineDecision, dict]] = []
        for event in events:
            if isinstance(event, FlagEvent):
                self.flag(s, event.wallet_id, event.ts, event.reason, event.source)
                results.append(Result(None, "flagged"))
                continue
            t = event.txn
            if t.txn_id in seen:
                results.append(Result(t.txn_id, "duplicate", duplicate=True))
                continue
            if t.ts < self.engine.last_ts - STALE_SECONDS:
                results.append(Result(t.txn_id, "stale", status_reason="behind_stream"))
                continue
            seen.add(t.txn_id)
            self.clock.observe(t.ts)
            result, row, decision_row = self._one(event)
            txn_rows.append(row)
            results.append(result)
            if decision_row is not None:
                decided.append((result, t, *decision_row))

        if self._new_wallets:
            s.execute(pg_insert(Wallet).on_conflict_do_nothing(), self._new_wallets)
        if self._new_agents:
            s.execute(pg_insert(Agent).on_conflict_do_nothing(), self._new_agents)
        if txn_rows:
            s.execute(insert(Transaction), txn_rows)
        decision_rows = []
        for result, t, decision, row in decided:
            if decision.tier != "allow":
                row["case_id"] = self._attach_to_case(s, t, decision.tier)
                result.decision["case_id"] = result.alert["case_id"] = row["case_id"]
            decision_rows.append(row)
        if decision_rows:
            s.flush()
            s.execute(insert(Decision), decision_rows)
        if self._shadow_rows:
            s.execute(insert(ShadowScore), self._shadow_rows)
        for result in results:
            if result.duplicate:
                self._recorded(s, result)
        return results

    def _one(self, event: TxnEvent) -> tuple[Result, dict, tuple[EngineDecision, dict] | None]:
        t, when = event.txn, moment(event.txn.ts)
        row = {
            "txn_id": t.txn_id, "ts": when, "type": t.type,
            "sender_id": t.sender_id, "sender_type": t.sender_type,
            "receiver_id": t.receiver_id, "receiver_type": t.receiver_type,
            "amount": t.amount,
            "sender_balance_before": None if math.isnan(t.balance_before) else t.balance_before,
            "device_id": t.device_id, "channel": t.channel, "district": t.district,
            "network": t.network, "source": event.source,
            "status": "completed", "status_reason": None,
            "applied_at": None, "applied_seq": None,
        }  # fmt: skip
        parties = ((t.sender_id, t.sender_type), (t.receiver_id, t.receiver_type))
        if any(kind == "wallet" and wallet in self.frozen for wallet, kind in parties):
            # A frozen wallet is a decision two people made; the model is not consulted.
            row["status"], row["status_reason"] = "rejected", "wallet_frozen"
            return Result(t.txn_id, "rejected", status_reason="wallet_frozen"), row, None
        self._register(t)
        if t.type not in SCORED_TYPES:
            self._apply(t)
            row["applied_at"], row["applied_seq"] = when, self.seq
            return Result(t.txn_id, "completed"), row, None

        t0 = time.perf_counter()
        features = self.engine.features(t)
        decision = self.decision.decide(t, features, context_from_engine(self.engine, t))
        latency_ms = (time.perf_counter() - t0) * 1000
        if self.shadow is not None and decision.risk is not None:
            self._shadow_score(t.txn_id, features)  # after the clock stopped: not in latency_ms

        tier = decision.tier
        if tier == "hold":
            status = "held"
        elif tier == "allow" or event.source == "replay":
            status = "completed"  # in a replay the customer's choice is already history
        else:
            status = "pending_customer"
        if status == "completed" or event.source == "replay":
            self._apply(t)
            row["applied_at"], row["applied_seq"] = when, self.seq
        row["status"] = status
        mule = decision.scores.get("mule")
        if mule is not None and mule >= self.decision.mule_threshold:
            self.mule_scores[t.receiver_id] = max(mule, self.mule_scores.get(t.receiver_id, 0.0))

        full = decision.to_dict()
        alert = tier != "allow"
        headline = None
        if alert:
            detail = {key: full[key] for key in _ALERT_DETAIL}
            headline = next((r for r in decision.reasons if r["direction"] == "raises"), None)
            detail["headline"] = headline and {
                "en": headline["title_en"],
                "bn": headline["title_bn"],
            }
        else:  # an allowed transaction keeps only which rules it went through
            trace = [{"id": r["id"], "status": r["status"]} for r in full["rule_trace"]]
            detail = {"rule_trace": trace, "segment": full["segment"]}
        decision_row = {
            "txn_id": t.txn_id, "tier": tier, "action": decision.action,
            "requires_review": decision.requires_review, "mode": decision.mode,
            "decided_by": decision.decided_by, "model_tier": decision.model_tier,
            "risk": decision.risk, "risk_score": decision.risk_score,
            "risk_band": decision.risk_band, "scores": clean(decision.scores),
            "model_version": decision.model_version, "policy_version": decision.policy_version,
            "scenario": decision.scenario, "detail": clean(detail),
            "narrative": decision.narrative, "features": [float(v) for v in features],
            "latency_ms": latency_ms, "case_id": None, "decided_at": when,
        }  # fmt: skip
        result = Result(t.txn_id, status, scored=True, decision=self.summary(decision_row))
        if alert:
            result.alert = {
                "txn_id": t.txn_id, "ts": when.isoformat(), "type": t.type, "amount": t.amount,
                "sender_id": t.sender_id, "receiver_id": t.receiver_id, "district": t.district,
                "tier": tier, "risk_score": decision.risk_score, "decided_by": decision.decided_by,
                "scenario": decision.scenario, "status": status, "case_id": None,
                "headline_en": headline["title_en"] if headline else None,
                "headline_bn": headline["title_bn"] if headline else None,
            }  # fmt: skip
        return result, row, (decision, decision_row)

    def _register(self, t: Txn) -> None:
        """A wallet or agent with no master record is registered on first sight, in the
        database too, so that a restart rebuilds exactly the same state."""
        engine = self.engine
        for party, kind in ((t.sender_id, t.sender_type), (t.receiver_id, t.receiver_type)):
            if kind == "wallet" and party not in engine.wallets:
                when = moment(t.ts)
                engine.register_wallet(party, when.timestamp(), t.district)
                self._new_wallets.append(
                    {
                        "wallet_id": party,
                        "created_at": when,
                        "district": t.district,
                        "area_type": "unknown",
                        "channel": t.channel if t.channel in ("app", "ussd") else "unknown",
                        "segment": "unknown",
                    }
                )
            elif kind == "agent" and party not in engine.agents:
                engine.register_agent(party, t.district)
                self._new_agents.append({"agent_id": party, "district": t.district})

    def _apply(self, t: Txn) -> None:
        self.engine.update(t)
        self.seq += 1

    def summary(self, row) -> dict:
        """The part of a decision the calling channel needs: what to do, not why."""
        get = row.get if isinstance(row, dict) else lambda key: getattr(row, key)
        spec = self.policy.tiers[get("tier")]
        return {
            "tier": get("tier"),
            "action": get("action"),
            "requires_review": get("requires_review"),
            "risk_score": get("risk_score"),
            "risk_band": get("risk_band"),
            "mode": get("mode"),
            "model_version": get("model_version"),
            "policy_version": get("policy_version"),
            "customer_message": get("detail").get("customer_message"),
            "cooling_off_minutes": spec.cooling_off_minutes,
            "review_sla_minutes": spec.review_sla_minutes,
            "case_id": get("case_id"),
            "latency_ms": round(get("latency_ms"), 2),
        }

    def _recorded(self, s: Session, result: Result) -> None:
        """Fill a duplicate's result with what was recorded the first time."""
        txn = s.get(Transaction, result.txn_id)
        decision = s.get(Decision, result.txn_id)
        result.status, result.status_reason = txn.status, txn.status_reason
        if decision is not None:
            result.scored, result.decision = True, self.summary(decision)

    def _attach_to_case(self, s: Session, t: Txn, tier: str) -> int | None:
        """Holds open a case on the wallet under suspicion; lesser alerts join an open one."""
        subject = t.receiver_id if t.type == "SEND_MONEY" else t.sender_id
        when = moment(t.ts)
        sla = self.policy.tiers["hold"].review_sla_minutes
        due = when + timedelta(minutes=sla) if sla and tier == "hold" else None
        case = s.scalar(select(Case).where(Case.subject_id == subject, Case.status != "closed"))
        if case is None:
            if tier != "hold":
                return None
            case = Case(
                subject_id=subject, priority=tier, source="alert", opened_at=when, sla_due_at=due
            )
            s.add(case)
            s.flush()
            kind = "opened"
        else:
            if RANK[tier] > RANK[case.priority]:
                case.priority = tier
            if case.sla_due_at is None:
                case.sla_due_at = due
            kind = "alert_added"
        s.add(CaseEvent(case_id=case.id, kind=kind, data={"txn_id": t.txn_id, "tier": tier}))
        return case.id

    def _publish(self, alerts: list[dict]) -> None:
        if not alerts or self.redis is None:
            return
        try:
            pipe = self.redis.pipeline(transaction=False)
            for alert in alerts:
                pipe.publish(self.settings.alerts_channel, json.dumps(alert))
            pipe.execute()
        except Exception:
            # The feed is a convenience; the alert is already in the database.
            log.exception("could not publish %d alerts to the live feed", len(alerts))

    # ------------------------------------------- state changes from the workflow

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        """A workflow change that may touch the state: serialised with scoring, and
        committed as one. Check everything first and raise `WorkflowError` before
        changing the engine; any other failure rebuilds the state from the database."""
        with self.lock:
            if not self.ready:
                self.recover()
            with self.sessions() as s:
                try:
                    yield s
                    s.commit()
                except WorkflowError:
                    s.rollback()
                    raise
                except Exception:
                    s.rollback()
                    self.ready = False
                    self.recover()
                    raise

    def now(self) -> datetime:
        return moment(self.clock.now())

    # The methods below are called inside `transaction()`.

    def flag(
        self,
        s: Session,
        wallet_id: str,
        ts: float,
        reason: str,
        source: str,
        case_id: int | None = None,
    ) -> None:
        """Record a wallet as confirmed fraud. The first confirmation stands."""
        self.clock.observe(ts)
        if wallet_id in self.engine.flagged:
            return
        when = moment(ts)
        ts = when.timestamp()  # exactly what recovery will read back
        self.seq += 1
        s.execute(
            pg_insert(WalletFlag)
            .values(
                wallet_id=wallet_id,
                flagged_at=when,
                reason=reason,
                source=source,
                case_id=case_id,
                applied_seq=self.seq,
            )
            .on_conflict_do_nothing()
        )
        self.engine.flag_wallet(wallet_id, ts)

    def complete(self, txn: Transaction) -> None:
        """A pending or held transaction goes through: apply it to the state now."""
        if txn.sender_id in self.frozen or txn.receiver_id in self.frozen:
            txn.status, txn.status_reason = "rejected", "wallet_frozen"
            return
        txn.status = "completed"
        if txn.applied_at is None:
            when = moment(max(self.clock.now(), self.engine.last_ts))
            self._apply(txn_from_row(txn, when.timestamp()))
            txn.applied_at, txn.applied_seq = when, self.seq

    def what_if(self, t: Txn) -> EngineDecision:
        """The decision this transaction would get now. Nothing is stored or changed."""
        with self.lock:
            known = {"wallet": self.engine.wallets, "agent": self.engine.agents}
            for party, kind in ((t.sender_id, t.sender_type), (t.receiver_id, t.receiver_type)):
                if party not in known[kind]:
                    raise KeyError(party)  # scoring it would create state for it
            features = self.engine.features(t)
            return self.decision.decide(t, features, context_from_engine(self.engine, t))
