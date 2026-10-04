"""How database rows are shown in responses."""

from __future__ import annotations

from datetime import datetime

from ..intel.attribute import categorise
from ..intel.taxonomy import load_taxonomy
from ..platform.cases import sla_state
from ..platform.models import Case, CaseEvent, Decision, FreezeRequest, Transaction, User
from ..platform.scoring import Result


def user_view(u: User) -> dict:
    return {"id": u.id, "username": u.username, "display_name": u.display_name, "role": u.role}


def result_view(r: Result) -> dict:
    return {
        "txn_id": r.txn_id,
        "status": r.status,
        "status_reason": r.status_reason,
        "scored": r.scored,
        "duplicate": r.duplicate,
        "decision": r.decision,
    }


def case_view(c: Case, now: datetime, names: dict[int, str] | None = None) -> dict:
    names = names or {}
    return {
        "id": c.id,
        "subject_id": c.subject_id,
        "status": c.status,
        "priority": c.priority,
        "source": c.source,
        "assigned_to": c.assigned_to,
        "assignee": names.get(c.assigned_to),
        "opened_at": c.opened_at,
        "sla_due_at": c.sla_due_at,
        "overdue": c.status != "closed" and c.sla_due_at is not None and c.sla_due_at < now,
        "sla_state": sla_state(c, now),
        "sla_remaining_seconds": (
            round((c.sla_due_at - now).total_seconds())
            if c.status != "closed" and c.sla_due_at is not None
            else None
        ),
        "verdict": c.verdict,
        "reason_code": c.reason_code,
        "closed_at": c.closed_at,
        "closed_by": c.closed_by,
        "closer": names.get(c.closed_by),
    }


def event_view(e: CaseEvent, names: dict[int, str]) -> dict:
    return {
        "id": e.id,
        "kind": e.kind,
        "actor": names.get(e.actor_id) if e.actor_id else "system",
        "body": e.body,
        "data": e.data,
        "at": e.created_at,
    }


def freeze_view(r: FreezeRequest, names: dict[int, str] | None = None) -> dict:
    names = names or {}
    return {
        "id": r.id,
        "wallet_id": r.wallet_id,
        "case_id": r.case_id,
        "reason": r.reason,
        "status": r.status,
        "requested_by": r.requested_by,
        "requester": names.get(r.requested_by),
        "decided_by": r.decided_by,
        "decider": names.get(r.decided_by),
        "decision_note": r.decision_note,
        "decided_at": r.decided_at,
        "created_at": r.created_at,
    }


def alert_view(t: Transaction, d: Decision) -> dict:
    """One line of the alert queue."""
    return {
        "txn_id": t.txn_id,
        "ts": t.ts,
        "type": t.type,
        "amount": t.amount,
        "sender_id": t.sender_id,
        "receiver_id": t.receiver_id,
        "district": t.district,
        "status": t.status,
        "status_reason": t.status_reason,
        "tier": d.tier,
        "risk_score": d.risk_score,
        "risk_band": d.risk_band,
        "mode": d.mode,
        "decided_by": d.decided_by,
        "scenario": d.scenario,
        "case_id": d.case_id,
        "customer_response": d.customer_response,
        "headline": d.detail.get("headline"),
    }


def decision_view(t: Transaction, d: Decision) -> dict:
    """Everything recorded about one decision: what happened, why, what next."""
    return {
        "transaction": {
            "txn_id": t.txn_id,
            "ts": t.ts,
            "type": t.type,
            "sender_id": t.sender_id,
            "receiver_id": t.receiver_id,
            "amount": t.amount,
            "sender_balance_before": t.sender_balance_before,
            "channel": t.channel,
            "district": t.district,
            "status": t.status,
            "status_reason": t.status_reason,
            "source": t.source,
        },
        "tier": d.tier,
        "action": d.action,
        "requires_review": d.requires_review,
        "mode": d.mode,
        "decided_by": d.decided_by,
        "model_tier": d.model_tier,
        "risk_score": d.risk_score,
        "risk_band": d.risk_band,
        "scores": d.scores,
        "model_version": d.model_version,
        "policy_version": d.policy_version,
        "scenario": d.scenario,
        "case_id": d.case_id,
        "customer_response": d.customer_response,
        "responded_at": d.responded_at,
        "decided_at": d.decided_at,
        "latency_ms": round(d.latency_ms, 2),
        # Named at read time from what the decision already recorded; see intel/attribute.py.
        "fraud_categories": categorise(
            d.scenario, d.scores, d.detail.get("similar_cases"), load_taxonomy()
        ),
        **{
            key: d.detail.get(key)
            for key in (
                "rule_trace",
                "fallback_signals",
                "reasons",
                "customer_message",
                "recommended_actions",
                "similar_cases",
                "headline",
            )
        },  # fmt: skip
    }
