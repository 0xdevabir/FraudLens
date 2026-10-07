"""Customer appeals: filed by the customer's app, answered by a reviewer within a deadline."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query
from sqlalchemy import case as sql_case
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...platform import appeals as workflow
from ...platform.audit import WorkflowError
from ...platform.models import Appeal, Transaction, User
from ..deps import Db, Plat, Platform, Reviewer, RowId, Service, TxnId
from ..schemas import AppealDecision, AppealIn
from ..views import appeal_view
from .customer import _limit

router = APIRouter(tags=["appeals"])

_TIER = sql_case({"hold": 3, "step_up": 2, "warn": 1}, value=Appeal.tier, else_=0)


def customer_appeal_view(p: Platform, s: Session, appeal: Appeal) -> dict:
    """What the customer's app shows: where the appeal stands, never who or why internally."""
    txn = s.get(Transaction, appeal.txn_id)
    return {
        "id": appeal.id,
        "txn_id": appeal.txn_id,
        "tier": appeal.tier,
        "status": appeal.status,
        "filed_at": appeal.filed_at,
        "sla_due_at": appeal.sla_due_at,
        "decided_at": appeal.decided_at,
        "txn_status": txn.status if txn else None,
        "now": p.scorer.now(),
    }


def find_appeal(s: Session, wallet_id: str, txn_id: int) -> Appeal:
    appeal = s.scalar(select(Appeal).where(Appeal.txn_id == txn_id))
    # The same answer for "no appeal" and "not yours".
    if appeal is None or appeal.wallet_id != wallet_id:
        raise WorkflowError(404, "appeal_not_found", "no appeal for this payment")
    return appeal


# ------------------------------------------------------------- the customer's app


@router.post("/customer/transactions/{txn_id}/appeal", status_code=201)
def file_appeal(txn_id: TxnId, body: AppealIn, p: Plat, s: Db, ctx: Service) -> dict:
    """'This is a genuine payment.' A person answers it; it decides nothing by itself."""
    _limit(p.report_limit, f"appeal:{body.wallet_id}")
    appeal = workflow.file_appeal(p.scorer, ctx, body.wallet_id, txn_id, body.relation, body.reason)
    return customer_appeal_view(p, s, appeal)


@router.get("/customer/transactions/{txn_id}/appeal")
def appeal_status(
    txn_id: TxnId,
    p: Plat,
    s: Db,
    ctx: Service,
    wallet_id: Annotated[str, Query(pattern=r"^[A-Za-z0-9_-]{1,32}$")],
) -> dict:
    return customer_appeal_view(p, s, find_appeal(s, wallet_id, txn_id))


# ------------------------------------------------------------------ the reviewers


@router.get("/appeals")
def list_appeals(
    p: Plat,
    s: Db,
    ctx: Reviewer,
    status: Literal["pending", "approved", "rejected"] | None = "pending",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    """The queue: money waiting first, then the nearest deadline."""
    where = [Appeal.status == status] if status else []
    rows = s.execute(
        select(Appeal, Transaction)
        .join(Transaction, Transaction.txn_id == Appeal.txn_id)
        .where(*where)
        .order_by(
            (Appeal.status != "pending"),
            (Transaction.status != "held"),
            _TIER.desc(),
            Appeal.sla_due_at.asc(),
            Appeal.id.desc(),
        )
        .limit(limit)
    ).all()
    ids = {a.decided_by for a, _ in rows if a.decided_by is not None}
    names = (
        dict(s.execute(select(User.id, User.username).where(User.id.in_(ids))).all()) if ids else {}
    )
    counts = dict(s.execute(select(Appeal.status, func.count()).group_by(Appeal.status)).all())
    now = p.scorer.now()
    return {
        "now": now,
        "counts": {k: counts.get(k, 0) for k in ("pending", "approved", "rejected")},
        "appeals": [
            appeal_view(a, now, names)
            | {
                "amount": t.amount,
                "receiver_id": t.receiver_id,
                "txn_status": t.status,
                "ts": t.ts,
            }
            for a, t in rows
        ],
    }


@router.post("/appeals/{appeal_id}/approve")
def approve(appeal_id: RowId, body: AppealDecision, p: Plat, ctx: Reviewer) -> dict:
    """The payment is genuine: a held one is released now."""
    done = workflow.decide_appeal(p.scorer, ctx, appeal_id, True, body.note)
    return {"appeal": appeal_view(done["appeal"], p.scorer.now()), **_outcome(done)}


@router.post("/appeals/{appeal_id}/reject")
def reject(appeal_id: RowId, body: AppealDecision, p: Plat, ctx: Reviewer) -> dict:
    """Not convinced: a held payment stays held, and its case decides."""
    done = workflow.decide_appeal(p.scorer, ctx, appeal_id, False, body.note)
    return {"appeal": appeal_view(done["appeal"], p.scorer.now()), **_outcome(done)}


def _outcome(done: dict) -> dict:
    return {"txn_status": done["txn_status"], "released": done["released"]}
