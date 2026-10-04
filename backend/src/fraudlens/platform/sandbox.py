"""Canned answers for sandbox API keys.

A partner building an integration needs every outcome on demand without moving real
state. With a sandbox key `/v1/score` ignores the models and decides by the cents of the
amount; nothing is stored, no case is opened, no webhook or SMS goes out.

    amount ends in .01 -> allow     .02 -> warn     .03 -> step_up     .04 -> hold
    any other amount   -> allow
"""

from __future__ import annotations

from ..decision.policy import Policy
from .events import TxnIn

_CENTS = {1: "allow", 2: "warn", 3: "step_up", 4: "hold"}
_STATUS = {
    "allow": "completed",
    "warn": "pending_customer",
    "step_up": "pending_customer",
    "hold": "held",
}
_BAND = {"allow": "low", "warn": "elevated", "step_up": "high", "hold": "very high"}
_SCORE = {"allow": 10, "warn": 45, "step_up": 65, "hold": 90}
_ACTION = {
    "allow": "proceed",
    "warn": "show_warning",
    "step_up": "step_up_auth",
    "hold": "hold_for_review",
}


def tier_for(amount: float) -> str:
    return _CENTS.get(round(amount * 100) % 100, "allow")


def score(body: TxnIn, policy: Policy) -> dict:
    """The response `/v1/score` would give, for the tier the amount asks for."""
    tier = tier_for(body.amount)
    spec = policy.tiers[tier]
    message = policy.messages["scam"][tier].model_dump() if tier != "allow" else None
    return {
        "txn_id": body.txn_id,
        "status": _STATUS[tier],
        "status_reason": None,
        "scored": True,
        "duplicate": False,
        "sandbox": True,
        "decision": {
            "tier": tier,
            "action": _ACTION[tier],
            "requires_review": spec.human_review,
            "risk_score": _SCORE[tier],
            "risk_band": _BAND[tier],
            "mode": "sandbox",
            "model_version": None,
            "policy_version": policy.version,
            "customer_message": message,
            "cooling_off_minutes": spec.cooling_off_minutes,
            "review_sla_minutes": spec.review_sla_minutes,
            "case_id": None,
            "latency_ms": 0.0,
        },
    }
