"""Refunds for scam victims: the victim's app follows its claim, reviewers see the queue."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...platform import refunds as workflow
from ...platform.audit import WorkflowError
from ...platform.models import Refund, User
from ..deps import Db, Plat, Platform, Reviewer, RowId, Service, TxnId
from ..schemas import RefundDecline
from ..views import customer_refund_view, refund_view

router = APIRouter(tags=["refunds"])

STATUSES = ("open", "paid", "unrecoverable", "declined")


def find_refund(s: Session, wallet_id: str, txn_id: int) -> Refund:
    refund = s.scalar(select(Refund).where(Refund.txn_id == txn_id))
    # The same answer for "no claim" and "not yours".
    if refund is None or refund.victim_id != wallet_id:
        raise WorkflowError(404, "refund_not_found", "no refund claim for this payment")
    return refund


def customer_view(p: Platform, refund: Refund) -> dict:
    return customer_refund_view(refund, refund.wallet_id in p.scorer.frozen, p.scorer.now())


# ------------------------------------------------------------- the customer's app


@router.get("/customer/transactions/{txn_id}/refund")
def refund_status(
    txn_id: TxnId,
    p: Plat,
    s: Db,
    ctx: Service,
    wallet_id: Annotated[str, Query(pattern=r"^[A-Za-z0-9_-]{1,32}$")],
) -> dict:
    """Where the victim's refund stands. The claim itself is made by reporting the payment."""
    return customer_view(p, find_refund(s, wallet_id, txn_id))


# ------------------------------------------------------------------ the reviewers


@router.get("/refunds")
def list_refunds(
    p: Plat,
    s: Db,
    ctx: Reviewer,
    status: Literal["open", "paid", "unrecoverable", "declined"] | None = "open",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    """The queue: the nearest promise to the customer first."""
    where = [Refund.status == status] if status else []
    rows = s.scalars(
        select(Refund)
        .where(*where)
        .order_by(Refund.sla_due_at.asc(), Refund.id.desc())
        .limit(limit)
    ).all()
    ids = {r.settled_by for r in rows if r.settled_by is not None}
    names = (
        dict(s.execute(select(User.id, User.username).where(User.id.in_(ids))).all()) if ids else {}
    )
    counts = dict(s.execute(select(Refund.status, func.count()).group_by(Refund.status)).all())
    paid = s.scalar(
        select(func.coalesce(func.sum(Refund.amount_refunded), 0)).where(Refund.status == "paid")
    )
    now = p.scorer.now()
    return {
        "now": now,
        "counts": {k: counts.get(k, 0) for k in STATUSES},
        "refunded_total": float(paid),
        "refunds": [
            refund_view(r, now, names) | {"wallet_frozen": r.wallet_id in p.scorer.frozen}
            for r in rows
        ],
    }


@router.post("/refunds/{refund_id}/decline")
def decline(refund_id: RowId, body: RefundDecline, p: Plat, ctx: Reviewer) -> dict:
    """Take one claim out of a case: the claimant is not a victim. No money moves."""
    return refund_view(workflow.decline(p.scorer, ctx, refund_id, body.note), p.scorer.now())
