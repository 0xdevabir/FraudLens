"""Investigations: the case queue, verdicts, and the two-person wallet freeze."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query
from sqlalchemy import case as sql_case
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...intel.attribute import categorise_report
from ...intel.taxonomy import load_taxonomy
from ...platform import cases as workflow
from ...platform.audit import WorkflowError
from ...platform.events import Identifier
from ...platform.models import (
    Case,
    CaseEvent,
    CustomerReport,
    Decision,
    FreezeRequest,
    Transaction,
    User,
)
from ...platform.pii import redact
from ..deps import REVIEWERS, Db, Plat, Reviewer, RowId, Supervisor
from ..schemas import (
    Assign,
    Escalate,
    FreezeDecision,
    FreezeRequestIn,
    Note,
    OpenCase,
    Unfreeze,
    Verdict,
)
from ..views import alert_view, case_view, event_view, freeze_view, user_view

router = APIRouter(tags=["cases"])

_PRIORITY = sql_case({"hold": 3, "step_up": 2, "warn": 1}, value=Case.priority, else_=0)


def _names(s: Session, ids) -> dict[int, str]:
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    return dict(s.execute(select(User.id, User.username).where(User.id.in_(ids))).all())


@router.get("/users/reviewers")
def reviewers(s: Db, ctx: Reviewer) -> list[dict]:
    """Who a case can be assigned to."""
    users = s.scalars(
        select(User).where(User.role.in_(REVIEWERS), User.is_active).order_by(User.username)
    )
    return [user_view(u) for u in users]


@router.get("/cases")
def list_cases(
    p: Plat,
    s: Db,
    ctx: Reviewer,
    status: Annotated[
        list[Literal["open", "in_review", "escalated", "closed"]] | None, Query()
    ] = None,
    assigned: Literal["me", "unassigned"] | None = None,
    subject_id: Identifier | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> dict:
    where = []
    if status:
        where.append(Case.status.in_(status))
    if assigned == "me":
        where.append(Case.assigned_to == ctx.user_id)
    elif assigned == "unassigned":
        where.append(Case.assigned_to.is_(None))
    if subject_id is not None:
        where.append(Case.subject_id == subject_id)
    held = Transaction.status == "held"
    alerts = (
        select(
            Decision.case_id,
            func.count().label("alerts"),
            func.count().filter(held).label("held"),
            func.coalesce(func.sum(Transaction.amount).filter(held), 0).label("held_amount"),
        )
        .join(Transaction, Transaction.txn_id == Decision.txn_id)
        .where(Decision.case_id.is_not(None))
        .group_by(Decision.case_id)
        .subquery()
    )
    rows = s.execute(
        select(Case, alerts.c.alerts, alerts.c.held, alerts.c.held_amount)
        .outerjoin(alerts, alerts.c.case_id == Case.id)
        .where(*where)
        # Work first: open before closed, then the most severe, then the nearest deadline.
        .order_by(
            (Case.status == "closed"),
            _PRIORITY.desc(),
            Case.sla_due_at.asc().nulls_last(),
            Case.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    ).all()
    total = s.scalar(select(func.count()).select_from(Case).where(*where))
    names = _names(s, [c.assigned_to for c, *_ in rows])
    now = p.scorer.now()
    return {
        "total": total,
        "cases": [
            {
                **case_view(c, now, names),
                "alerts": n or 0,
                "held": n_held or 0,
                "held_amount": float(amount or 0),
            }
            for c, n, n_held, amount in rows
        ],
    }


@router.post("/cases", status_code=201)
def open_case(body: OpenCase, p: Plat, ctx: Reviewer) -> dict:
    return case_view(workflow.open_case(p.scorer, ctx, body.wallet_id, body.reason), p.scorer.now())


@router.get("/cases/{case_id}")
def get_case(case_id: RowId, p: Plat, s: Db, ctx: Reviewer) -> dict:
    case = s.get(Case, case_id)
    if case is None:
        raise WorkflowError(404, "case_not_found", f"no case {case_id}")
    events = s.scalars(
        select(CaseEvent).where(CaseEvent.case_id == case_id).order_by(CaseEvent.id)
    ).all()
    alerts = s.execute(
        select(Transaction, Decision)
        .join(Decision, Decision.txn_id == Transaction.txn_id)
        .where(Decision.case_id == case_id)
        .order_by(Decision.decided_at.desc(), Decision.txn_id.desc())
        .limit(200)
    ).all()
    freezes = s.scalars(
        select(FreezeRequest).where(FreezeRequest.case_id == case_id).order_by(FreezeRequest.id)
    ).all()
    reports = s.scalars(
        select(CustomerReport).where(CustomerReport.case_id == case_id).order_by(CustomerReport.id)
    ).all()
    people = [case.assigned_to, case.closed_by, *(e.actor_id for e in events)]
    people += [who for f in freezes for who in (f.requested_by, f.decided_by)]
    names = _names(s, people)
    return {
        **case_view(case, p.scorer.now(), names),
        "subject": p.graph.risk(case.subject_id),
        "alerts": [alert_view(t, d) for t, d in alerts],
        "timeline": [
            event_view(e, names) | ({"body": redact(e.body)} if e.kind == "customer_report" else {})
            for e in events
        ],
        "freeze_requests": [freeze_view(f, names) for f in freezes],
        "customer_reports": [
            {
                "id": r.id,
                "reporter_id": r.reporter_id,
                "txn_id": r.txn_id,
                "category": r.category,
                # Typed by a customer: numbers and addresses in it are masked, and
                # nothing reads it to decide.
                "description": redact(r.description),
                # A label for the analyst. The model reads the text; it changes nothing.
                "fraud_categories": categorise_report(
                    r.category, r.description, p.intel, load_taxonomy()
                ),
                "reported_at": r.reported_at,
            }
            for r in reports
        ],
    }


@router.post("/cases/{case_id}/assign")
def assign(case_id: RowId, body: Assign, p: Plat, ctx: Reviewer) -> dict:
    case = workflow.assign(p.sessions, ctx, case_id, body.assignee_id or ctx.user_id)
    return case_view(case, p.scorer.now())


@router.post("/cases/{case_id}/notes", status_code=201)
def add_note(case_id: RowId, body: Note, p: Plat, ctx: Reviewer) -> dict:
    return event_view(workflow.add_note(p.sessions, ctx, case_id, body.body), {}) | {
        "actor": ctx.username
    }


@router.post("/cases/{case_id}/escalate")
def escalate(case_id: RowId, body: Escalate, p: Plat, ctx: Reviewer) -> dict:
    return case_view(workflow.escalate(p.sessions, ctx, case_id, body.reason), p.scorer.now())


@router.post("/cases/{case_id}/verdict")
def verdict(case_id: RowId, body: Verdict, p: Plat, ctx: Reviewer) -> dict:
    """The human decision: closes the case and releases or blocks the held money."""
    outcome = workflow.give_verdict(p.scorer, ctx, case_id, body.verdict, body.note)
    return {
        "case": case_view(outcome["case"], p.scorer.now()),
        "released": outcome["released"],
        "blocked": outcome["blocked"],
    }


# ---------------------------------------------------------------- freezes


@router.post("/wallets/{wallet_id}/freeze-requests", status_code=201)
def request_freeze(wallet_id: Identifier, body: FreezeRequestIn, p: Plat, ctx: Reviewer) -> dict:
    """Ask for a wallet to be frozen. A second person has to approve it."""
    request = workflow.request_freeze(p.sessions, ctx, wallet_id, body.reason, body.case_id)
    return freeze_view(request)


@router.get("/freeze-requests")
def freeze_requests(
    s: Db,
    ctx: Reviewer,
    status: Literal["pending", "approved", "rejected"] | None = "pending",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict]:
    query = select(FreezeRequest).order_by(FreezeRequest.id.desc()).limit(limit)
    if status is not None:
        query = query.where(FreezeRequest.status == status)
    rows = s.scalars(query).all()
    names = _names(s, [who for r in rows for who in (r.requested_by, r.decided_by)])
    return [freeze_view(r, names) for r in rows]


@router.post("/freeze-requests/{request_id}/approve")
def approve_freeze(request_id: RowId, body: FreezeDecision, p: Plat, ctx: Supervisor) -> dict:
    return freeze_view(workflow.decide_freeze(p.scorer, ctx, request_id, True, body.note))


@router.post("/freeze-requests/{request_id}/reject")
def reject_freeze(request_id: RowId, body: FreezeDecision, p: Plat, ctx: Supervisor) -> dict:
    return freeze_view(workflow.decide_freeze(p.scorer, ctx, request_id, False, body.note))


@router.post("/wallets/{wallet_id}/unfreeze")
def unfreeze(wallet_id: Identifier, body: Unfreeze, p: Plat, ctx: Supervisor) -> dict:
    wallet = workflow.unfreeze(p.scorer, ctx, wallet_id, body.reason)
    return {"wallet_id": wallet.wallet_id, "status": wallet.status}
