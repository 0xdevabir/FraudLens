"""What the customer app needs, called by upay's backend on the customer's behalf.

The customer is authenticated by upay's app, not here: these endpoints take the
service account, and the wallet the request is for is part of the body.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy.orm import Session

from ...intel import proof
from ...intel.analyze import check_message
from ...intel.memory import explain as explain_link
from ...intel.taxonomy import load_taxonomy
from ...platform import blocklist as lists
from ...platform import cases as workflow
from ...platform.audit import WorkflowError
from ..deps import Db, Plat, Platform, Service, TxnId
from ..schemas import CustomerResponse, MessageCheck, PaymentVerify, RecipientCheck, ScamReport
from ..views import customer_refund_view

router = APIRouter(prefix="/customer", tags=["customer"])


def _limit(limiter, key: str) -> None:
    if not limiter.hit(key):
        raise WorkflowError(
            429, "rate_limited", "too many requests for this wallet",
            retry_after_seconds=limiter.window,
        )  # fmt: skip


@router.post("/recipient-check")
def recipient_check(body: RecipientCheck, p: Plat, ctx: Service) -> dict:
    """Before the customer types an amount: is there a reason to warn about this receiver?

    Advice only. The answer is a level and a fixed text from the policy; it never
    says what is known about the receiver, and it is rate-limited per sender so
    it cannot be used to probe which wallets have been caught.
    """
    _limit(p.check_limit, body.sender_id)
    return check_recipient(p, body.receiver_id, body.sender_id)


def check_recipient(p: Platform, receiver_id: str, sender_id: str | None = None) -> dict:
    risk = p.graph.risk(receiver_id)
    level = "none"
    if risk is not None:
        if risk["confirmed_fraud"] or receiver_id in p.scorer.frozen:
            level = "high"
        elif risk["mule_alert"]:
            level = "caution"
    # The sender's own flagged message named this wallet: say so before they type an amount.
    # `reasons` appears only then, so an ordinary answer is unchanged and reveals nothing new.
    link = None
    if sender_id is not None:
        link = p.scorer.messages.link(sender_id, receiver_id, None, p.scorer.now())
        if link is not None and level == "none":
            level = "caution"
    message = p.scorer.policy.messages["scam"]["warn"].model_dump() if level != "none" else None
    out = {"receiver_id": receiver_id, "level": level, "message": message}
    return out if link is None else out | {"reasons": [explain_link(link)]}


@router.post("/transactions/{txn_id}/respond")
def respond(txn_id: TxnId, body: CustomerResponse, p: Plat, ctx: Service) -> dict:
    """The customer's answer to a warning or a step-up: go ahead, or cancel."""
    txn = workflow.customer_respond(
        p.scorer, ctx, body.wallet_id, txn_id, body.action, body.step_up_passed
    )
    return {"txn_id": txn.txn_id, "status": txn.status, "status_reason": txn.status_reason}


@router.post("/reports", status_code=201)
def report(body: ScamReport, p: Plat, ctx: Service) -> dict:
    """'I think I was scammed.' Opens a case for an analyst; decides nothing by itself.

    Reporting a payment that went through also claims a refund for it: `refund` is
    where that claim stands (GET .../transactions/{txn_id}/refund follows it).
    """
    _limit(p.report_limit, body.reporter_id)
    return report_view(p, *workflow.report_scam(
        p.scorer, ctx, body.reporter_id, body.reported_wallet_id, body.txn_id,
        body.category, body.description,
    ))  # fmt: skip


def report_view(p: Platform, made, refund) -> dict:
    claim = None
    if refund is not None:
        claim = customer_refund_view(refund, refund.wallet_id in p.scorer.frozen, p.scorer.now())
    return {
        "report_id": made.id,
        "case_id": made.case_id,
        "reference": made.reference,
        "refund": claim,
    }


@router.post("/message-check")
def message_check(body: MessageCheck, p: Plat, s: Db, ctx: Service) -> dict:
    """'Is this message a scam?' A level, the kinds of fraud it looks like, and why.

    Advice only: nothing is blocked, opened or recorded because of the answer, and
    the text is not kept. What the customer is told is the taxonomy's fixed advice.
    """
    _limit(p.message_limit, body.wallet_id)
    return check_text(p, body.text, s, body.wallet_id)


def check_text(
    p: Platform, text: str, s: Session | None = None, wallet_id: str | None = None
) -> dict:
    """With `wallet_id`, a flagged message's wallet IDs and amounts (never its text) are
    kept for half an hour, so a payment that follows it is warned (`intel/memory.py`)."""
    listed = lists.in_text(s, text, p.scorer.now()) if s is not None else []
    found = check_message(text, p.intel, load_taxonomy(), listed)
    if wallet_id is not None:
        p.scorer.messages.remember(wallet_id, text, found, p.scorer.now())
    return found


@router.post("/payment-verify")
def payment_verify(body: PaymentVerify, p: Plat, s: Db, ctx: Service) -> dict:
    """'They say they paid me.' Answered from the ledger, not from the proof shown.

    Only payments made to the asking wallet can be verified; any other transaction
    is reported as not found, and the endpoint is rate-limited per wallet, so it
    cannot be used to look up other people's payments.
    """
    _limit(p.proof_limit, body.wallet_id)
    return verify_payment(p, s, body)


def verify_payment(p: Platform, s: Session, body: PaymentVerify) -> dict:
    claim = proof.read_claim(body.wallet_id, body.txn_id, body.amount, body.message)
    found = proof.verify(s, claim, p.scorer.now())
    return {
        **found,
        "claimed": {"txn_id": claim.txn_id, "amount": claim.amount},
        "message": load_taxonomy().proof_messages[found["status"]].model_dump(),
        # What the accompanying text asks for ("send the extra back") is a second, separate sign.
        "text": check_text(p, body.message, s) if body.message.strip() else None,
    }
