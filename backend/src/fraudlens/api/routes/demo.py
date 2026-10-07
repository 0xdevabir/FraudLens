"""Stand-ins for what upay's app already knows, so the console can demo the customer side.

A real channel knows the customer's handset, balance and district and sends them
with each payment. The demo phone does not, so these endpoints fill a payment in
from what the platform has seen. The second half plays the app itself: it sends
the payment, the customer's answer and a scam report through exactly the code
the service account's endpoints use, as the signed-in member of staff, and every
one of those calls is audited as a demo action.

The router is not registered when the environment is production.
"""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...decision.policy import TIERS
from ...features.engine import DISTRICT_COORDS, MIN_NETWORK_HISTORY
from ...platform import appeals as appeal_flow
from ...platform import cases as workflow
from ...platform.audit import WorkflowError, audit
from ...platform.events import Identifier, TxnIn
from ...platform.models import Decision, Transaction
from ..deps import Db, Plat, Platform, Staff
from ..schemas import (
    DemoAppeal,
    DemoClock,
    DemoPayment,
    DemoResponse,
    MessageCheck,
    PaymentVerify,
    RecipientCheck,
    ScamReport,
)
from ..views import result_view
from .appeals import customer_appeal_view, find_appeal
from .customer import _limit, check_recipient, check_text, verify_payment

router = APIRouter(prefix="/demo", tags=["demo"])

DEFAULT_BALANCE = 20_000.0
CANDIDATES = 40  # recent payments tried per tier
Amount = Annotated[float, Query(gt=0, le=10_000_000, allow_inf_nan=False)]


def _draft(
    p: Platform,
    s: Session,
    sender: str,
    receiver: str,
    amount: float,
    device: str | None = None,
    district: str | None = None,
    ip: str | None = None,
) -> dict:
    """A send-money event as the app would post it now: from the customer's home
    district unless the demo says they are somewhere else."""
    if district is not None and district not in DISTRICT_COORDS:
        raise WorkflowError(422, "unknown_district", "the demo knows only the listed districts")
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
        "district": district or state.home,
        "ip": ip,
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


@router.get("/habits")
def habits(sender_id: Identifier, p: Plat, ctx: Staff) -> dict:
    """The behaviour profile the platform holds for a wallet: where it usually
    transacts from, and how many of its payments carried a network."""
    state = p.scorer.engine.wallets.get(sender_id)
    if state is None:
        raise WorkflowError(404, "unknown_party", "the demo needs wallets the platform has seen")
    places = sorted(state.places.items(), key=lambda place: -place[1])
    return {
        "home": state.home,
        "transactions": state.n_init,
        "places": [{"district": d, "transactions": n} for d, n in places],
        "networks_seen": len(state.networks),
        "network_transactions": sum(state.networks.values()),
        "network_history_needed": MIN_NETWORK_HISTORY,
        "districts": sorted(DISTRICT_COORDS),
    }


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


# ------------------------------------------------- the customer's phone, played by staff


@router.post("/pay")
def pay(body: DemoPayment, p: Plat, s: Db, ctx: Staff) -> dict:
    """Send a payment as the demo customer. It is scored and recorded like any other."""
    if body.sender_id == body.receiver_id:
        raise WorkflowError(422, "same_party", "sender and receiver are the same")
    draft = _draft(
        p, s, body.sender_id, body.receiver_id, body.amount,
        district=body.district, ip=str(body.ip) if body.ip else None,
    )  # fmt: skip
    txn_id = time.time_ns() // 1_000  # unique enough for one person pressing a button
    [result] = p.scorer.process([TxnIn(txn_id=txn_id, **draft).event()])
    if result.status == "stale":
        raise WorkflowError(409, "stale_event", "the event stream is ahead of this payment")
    audit(
        s, ctx, "demo.payment", "transaction", txn_id,
        sender_id=body.sender_id, receiver_id=body.receiver_id, status=result.status,
    )  # fmt: skip
    s.commit()
    return result_view(result)


@router.post("/respond")
def respond(body: DemoResponse, p: Plat, s: Db, ctx: Staff) -> dict:
    """The demo customer's answer to a warning or a step-up."""
    sender = s.scalar(select(Transaction.sender_id).where(Transaction.txn_id == body.txn_id))
    if sender is None:
        raise WorkflowError(404, "txn_not_found", "no such transaction")
    txn = workflow.customer_respond(
        p.scorer, ctx, sender, body.txn_id, body.action, body.step_up_passed
    )
    return {"txn_id": txn.txn_id, "status": txn.status, "status_reason": txn.status_reason}


@router.post("/report", status_code=201)
def report(body: ScamReport, p: Plat, ctx: Staff) -> dict:
    """The demo customer's 'I think I was scammed'. Limited per wallet, like the real one."""
    _limit(p.report_limit, body.reporter_id)
    made = workflow.report_scam(
        p.scorer, ctx, body.reporter_id, body.reported_wallet_id, body.txn_id,
        body.category, body.description,
    )  # fmt: skip
    return {"report_id": made.id, "case_id": made.case_id}


def _sender(s: Session, txn_id: int) -> str:
    sender = s.scalar(select(Transaction.sender_id).where(Transaction.txn_id == txn_id))
    if sender is None:
        raise WorkflowError(404, "txn_not_found", "no such transaction")
    return sender


@router.post("/appeal", status_code=201)
def appeal(body: DemoAppeal, p: Plat, s: Db, ctx: Staff) -> dict:
    """The demo customer's 'this is a genuine payment'. A reviewer answers it on /appeals."""
    sender = _sender(s, body.txn_id)
    _limit(p.report_limit, f"appeal:{sender}")
    made = appeal_flow.file_appeal(p.scorer, ctx, sender, body.txn_id, body.relation, body.reason)
    return customer_appeal_view(p, s, made)


@router.get("/appeal")
def appeal_status(
    txn_id: Annotated[int, Query(ge=0, lt=2**62)], p: Plat, s: Db, ctx: Staff
) -> dict:
    """Where the demo customer's appeal stands, as their app would show it."""
    return customer_appeal_view(p, s, find_appeal(s, _sender(s, txn_id), txn_id))


@router.post("/recipient-check")
def recipient_check(body: RecipientCheck, p: Plat, ctx: Staff) -> dict:
    """What the app would show before the customer types an amount."""
    return check_recipient(p, body.receiver_id)


@router.post("/message-check")
def message_check(body: MessageCheck, p: Plat, s: Db, ctx: Staff) -> dict:
    """The demo customer asks whether a message is a scam. The text is not recorded."""
    _limit(p.message_limit, body.wallet_id)
    found = check_text(p, body.text)
    audit(
        s, ctx, "demo.message_checked", "wallet", body.wallet_id,
        level=found["level"], categories=[c["id"] for c in found["categories"]],
    )  # fmt: skip
    s.commit()
    return found


@router.post("/payment-verify")
def payment_verify(body: PaymentVerify, p: Plat, s: Db, ctx: Staff) -> dict:
    """The demo customer asks whether a payment they were shown proof of really arrived."""
    _limit(p.proof_limit, body.wallet_id)
    found = verify_payment(p, s, body)
    audit(s, ctx, "demo.payment_verified", "wallet", body.wallet_id, status=found["status"])
    s.commit()
    return found


@router.get("/payment-claims")
def payment_claims(p: Plat, s: Db, ctx: Staff) -> dict:
    """Ready claims for the payment-proof check, built on the newest completed payment:
    the real one, the same one with the amount edited, and a forged SMS. `expects`
    is what the check itself answers for each, so the label cannot go stale."""
    txn = s.scalars(
        select(Transaction)
        .where(Transaction.type == "SEND_MONEY", Transaction.status == "completed")
        .order_by(Transaction.ts.desc())
        .limit(1)
    ).first()
    if txn is None:
        return {"wallet_id": None, "claims": []}
    forged = (
        f"Cash In Tk {txn.amount + 4_000:,.2f} from 01711000000 successful. TrxID 8QW2ZX91LM. "
        "Bhai vul kore beshi taka chole geche, 4,000 taka ferot pathan please."
    )
    ready = {
        "real": (str(txn.txn_id), txn.amount, ""),
        "edited_amount": (str(txn.txn_id), min(txn.amount * 10, 10_000_000), ""),
        "forged_sms": (None, None, forged),
    }
    claims = []
    for name, (txn_id, amount, message) in ready.items():
        body = PaymentVerify(
            wallet_id=txn.receiver_id, txn_id=txn_id, amount=amount, message=message
        )
        expects = verify_payment(p, s, body)["status"]
        claims.append({"id": name, **body.model_dump(), "expects": expects})
    return {"wallet_id": txn.receiver_id, "claims": claims}


@router.post("/advance-clock")
def advance_clock(body: DemoClock, p: Plat, s: Db, ctx: Staff) -> dict:
    """Move the platform's clock forward, so a cooling-off period can be shown ending.

    Deadlines move with it: review times on open cases come closer by the same amount.
    """
    with p.scorer.lock:
        p.scorer.clock.observe(p.scorer.clock.now() + body.minutes * 60)
    audit(s, ctx, "demo.clock_advanced", "clock", None, minutes=body.minutes)
    s.commit()
    return {"now": p.scorer.now()}
