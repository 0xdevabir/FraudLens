"""What the customer app needs, called by upay's backend on the customer's behalf.

The customer is authenticated by upay's app, not here: these endpoints take the
service account, and the wallet the request is for is part of the body.
"""

from __future__ import annotations

from fastapi import APIRouter

from ...platform import cases as workflow
from ...platform.audit import WorkflowError
from ..deps import Plat, Service, TxnId
from ..schemas import CustomerResponse, RecipientCheck, ScamReport

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
    risk = p.graph.risk(body.receiver_id)
    level = "none"
    if risk is not None:
        if risk["confirmed_fraud"] or body.receiver_id in p.scorer.frozen:
            level = "high"
        elif risk["mule_alert"]:
            level = "caution"
    message = p.scorer.policy.messages["scam"]["warn"].model_dump() if level != "none" else None
    return {"receiver_id": body.receiver_id, "level": level, "message": message}


@router.post("/transactions/{txn_id}/respond")
def respond(txn_id: TxnId, body: CustomerResponse, p: Plat, ctx: Service) -> dict:
    """The customer's answer to a warning or a step-up: go ahead, or cancel."""
    txn = workflow.customer_respond(
        p.scorer, ctx, body.wallet_id, txn_id, body.action, body.step_up_passed
    )
    return {"txn_id": txn.txn_id, "status": txn.status, "status_reason": txn.status_reason}


@router.post("/reports", status_code=201)
def report(body: ScamReport, p: Plat, ctx: Service) -> dict:
    """'I think I was scammed.' Opens a case for an analyst; decides nothing by itself."""
    _limit(p.report_limit, body.reporter_id)
    made = workflow.report_scam(
        p.scorer, ctx, body.reporter_id, body.reported_wallet_id, body.txn_id,
        body.category, body.description,
    )  # fmt: skip
    return {"report_id": made.id, "case_id": made.case_id}
