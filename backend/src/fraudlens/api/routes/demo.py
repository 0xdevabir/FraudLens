"""Stand-ins for what upay's app already knows, so the console can demo the customer side.

A real channel knows the customer's handset, balance and district and sends them
with each payment. The demo phone does not, so these endpoints fill a payment in
from what the platform has seen. They decide nothing and change nothing, and the
router is not registered when the environment is production.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...decision.policy import TIERS
from ...platform.audit import WorkflowError
from ...platform.events import Identifier, TxnIn
from ...platform.models import Decision, Transaction
from ..deps import Db, Plat, Platform, Staff

router = APIRouter(prefix="/demo", tags=["demo"])

DEFAULT_BALANCE = 20_000.0
CANDIDATES = 40  # recent payments tried per tier
Amount = Annotated[float, Query(gt=0, le=10_000_000, allow_inf_nan=False)]


def _draft(
    p: Platform, s: Session, sender: str, receiver: str, amount: float, device: str | None = None
) -> dict:
    """A send-money event as the app would post it now."""
    state = p.scorer.engine.wallets.get(sender)
    if state is None or receiver not in p.scorer.engine.wallets:
        raise WorkflowError(404, "unknown_party", "the demo needs wallets the platform has seen")
    balance = s.scalar(
        select(Transaction.sender_balance_before)
        .where(Transaction.sender_id == sender, Transaction.sender_balance_before.is_not(None))
        .order_by(Transaction.ts.desc())
        .limit(1)
    )
    return {
        "ts": p.scorer.now().isoformat(),
        "type": "SEND_MONEY",
        "sender_id": sender,
        "sender_type": "wallet",
        "receiver_id": receiver,
        "receiver_type": "wallet",
        "amount": amount,
        "sender_balance_before": max(float(balance or DEFAULT_BALANCE), amount),
        "device_id": device if device is not None else state.last_device,
        "channel": "app",
        "district": state.home,
        "source": "live",
    }


def _tier(p: Platform, body: dict) -> str:
    return p.scorer.what_if(TxnIn(txn_id=0, **body).event().txn).tier


@router.get("/payment-draft")
def payment_draft(
    sender_id: Identifier, receiver_id: Identifier, amount: Amount, p: Plat, s: Db, ctx: Staff
) -> dict:
    """The payment the app would send for this customer, minus its `txn_id`."""
    if sender_id == receiver_id:
        raise WorkflowError(422, "same_party", "sender and receiver are the same")
    return _draft(p, s, sender_id, receiver_id, amount)


@router.get("/scenarios")
def scenarios(p: Plat, s: Db, ctx: Staff) -> dict:
    """One ready payment per tier, checked against the policy as it stands right now.

    Each is a recent payment between two wallets that would still get that answer
    today, plus one to a confirmed-fraud wallet to show a hard rule. A tier with no
    such payment is left out.
    """
    frozen = p.scorer.frozen
    found = []
    for tier in TIERS:
        rows = s.execute(
            select(
                Transaction.sender_id, Transaction.receiver_id,
                Transaction.amount, Transaction.device_id,
            )
            .join(Decision, Decision.txn_id == Transaction.txn_id)
            .where(Decision.tier == tier, Transaction.type == "SEND_MONEY")
            .order_by(Transaction.ts.desc())
            .limit(CANDIDATES)
        ).all()  # fmt: skip
        for sender, receiver, amount, device in rows:
            if sender in frozen or receiver in frozen:
                continue
            try:
                body = _draft(p, s, sender, receiver, float(amount), device)
                if _tier(p, body) == tier:
                    found.append({"id": tier, "expected_tier": tier, "payment": body})
                    break
            except (WorkflowError, KeyError):
                continue

    flagged = sorted(p.scorer.engine.flagged.keys() - frozen)
    payer = next((f["payment"]["sender_id"] for f in found if f["id"] == "allow"), None)
    if payer is not None:
        for receiver in flagged:
            try:
                body = _draft(p, s, payer, receiver, 1_000.0)
                found.append(
                    {"id": "confirmed_fraud", "expected_tier": _tier(p, body), "payment": body}
                )
                break
            except (WorkflowError, KeyError):
                continue
    return {"now": p.scorer.now(), "scenarios": found}
