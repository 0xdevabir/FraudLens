"""Investigations: the case queue, verdicts, and the two-person wallet freeze."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import case as sql_case
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ...intel.attribute import categorise_report
from ...intel.taxonomy import load_taxonomy
from ...platform import cases as workflow
from ...platform import refunds
from ...platform.audit import WorkflowError, audit
from ...platform.cases import AT_RISK_FRACTION, sla_state
from ...platform.events import Identifier
from ...platform.models import (
    Case,
    CaseEvent,
    CustomerReport,
    Decision,
    FreezeRequest,
    Refund,
    Transaction,
    User,
)
from ...platform.pii import mask_id, redact
from ..deps import REVIEWERS, Db, Plat, Reviewer, RowId, Supervisor
from ..export import MAX_ROWS, check_reveal, check_too_large, csv_response
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
from ..views import alert_view, case_view, event_view, freeze_view, refund_view, user_view

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


CaseStatus = Literal["open", "in_review", "escalated", "closed"]
Priority = Literal["hold", "step_up", "warn"]
SlaState = Literal["ok", "at_risk", "breached"]
FinalVerdict = Literal["confirmed_fraud", "legitimate", "inconclusive"]
CaseQ = Annotated[str | None, Query(max_length=32, pattern=r"^[A-Za-z0-9_-]+$")]


def _case_where(
    now, ctx, status, assigned, assignee_id, subject_id, priority, sla, verdict,
    opened_since, opened_until, q,
) -> list:  # fmt: skip
    where = []
    if status:
        where.append(Case.status.in_(status))
    if assigned == "me":
        where.append(Case.assigned_to == ctx.user_id)
    elif assigned == "unassigned":
        where.append(Case.assigned_to.is_(None))
    if assignee_id is not None:
        where.append(Case.assigned_to == assignee_id)
    if subject_id is not None:
        where.append(Case.subject_id == subject_id)
    if priority:
        where.append(Case.priority.in_(priority))
    if verdict:
        where.append(Case.verdict == verdict)
    if opened_since is not None:
        where.append(Case.opened_at >= opened_since)
    if opened_until is not None:
        where.append(Case.opened_at < opened_until)
    if sla:
        live = (Case.status != "closed") & Case.sla_due_at.is_not(None)
        due_at, window = (
            func.extract("epoch", Case.sla_due_at),
            func.extract("epoch", Case.sla_due_at - Case.opened_at),
        )
        risky_from = due_at - window * AT_RISK_FRACTION
        at = now.timestamp()
        states = {
            "breached": live & (Case.sla_due_at < now),
            "at_risk": live & (Case.sla_due_at >= now) & (risky_from <= at),
            "ok": live & (Case.sla_due_at >= now) & (risky_from > at),
        }
        where.append(or_(*(states[name] for name in sla)))
    if q:
        # A case number, or the start of the wallet id under investigation.
        match = [Case.subject_id.startswith(q, autoescape=True)]
        if q.isdigit() and len(q) < 10:
            match.append(Case.id == int(q))
        where.append(or_(*match))
    return where


def _case_alerts():
    held = Transaction.status == "held"
    return (
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


@router.get("/cases")
def list_cases(
    p: Plat,
    s: Db,
    ctx: Reviewer,
    status: Annotated[list[CaseStatus] | None, Query()] = None,
    assigned: Literal["me", "unassigned"] | None = None,
    assignee_id: Annotated[int | None, Query(ge=1, le=2**31 - 1)] = None,
    subject_id: Identifier | None = None,
    priority: Annotated[list[Priority] | None, Query()] = None,
    sla: Annotated[list[SlaState] | None, Query()] = None,
    verdict: FinalVerdict | None = None,
    opened_since: datetime | None = None,
    opened_until: datetime | None = None,
    q: CaseQ = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> dict:
    now = p.scorer.now()
    where = _case_where(
        now, ctx, status, assigned, assignee_id, subject_id, priority, sla, verdict,
        opened_since, opened_until, q,
    )  # fmt: skip
    alerts = _case_alerts()
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


CASE_COLUMNS = (
    "case_id", "status", "priority", "sla_state", "wallet_id", "assignee", "alerts",
    "held_payments", "held_amount", "opened_at", "sla_due_at", "verdict", "reason_code",
)  # fmt: skip


@router.get("/cases.csv")
def export_cases(
    p: Plat,
    s: Db,
    ctx: Reviewer,
    status: Annotated[list[CaseStatus] | None, Query()] = None,
    assigned: Literal["me", "unassigned"] | None = None,
    assignee_id: Annotated[int | None, Query(ge=1, le=2**31 - 1)] = None,
    subject_id: Identifier | None = None,
    priority: Annotated[list[Priority] | None, Query()] = None,
    sla: Annotated[list[SlaState] | None, Query()] = None,
    verdict: FinalVerdict | None = None,
    opened_since: datetime | None = None,
    opened_until: datetime | None = None,
    q: CaseQ = None,
    reveal: bool = False,
) -> StreamingResponse:
    """The case list as CSV, for the same filters. Masked unless a supervisor passes
    `reveal=true`. Every export is audited."""
    check_reveal(ctx.role, reveal)
    now = p.scorer.now()
    where = _case_where(
        now, ctx, status, assigned, assignee_id, subject_id, priority, sla, verdict,
        opened_since, opened_until, q,
    )  # fmt: skip
    total = s.scalar(select(func.count()).select_from(Case).where(*where))
    check_too_large(total)
    alerts = _case_alerts()
    rows = s.execute(
        select(Case, alerts.c.alerts, alerts.c.held, alerts.c.held_amount)
        .outerjoin(alerts, alerts.c.case_id == Case.id)
        .where(*where)
        .order_by(Case.id.desc())
        .limit(MAX_ROWS)
    ).all()
    names = _names(s, [c.assigned_to for c, *_ in rows])
    show = (lambda v: v) if reveal else mask_id
    out = [
        (
            c.id, c.status, c.priority, sla_state(c, now), show(c.subject_id),
            names.get(c.assigned_to), n or 0, n_held or 0, float(amount or 0),
            c.opened_at.isoformat(), c.sla_due_at and c.sla_due_at.isoformat(),
            c.verdict, c.reason_code,
        )
        for c, n, n_held, amount in rows
    ]  # fmt: skip
    audit(
        s, ctx, "export.cases", "cases", None,
        rows=len(out), revealed=reveal,
        filters={
            "status": status, "assigned": assigned, "priority": priority, "sla": sla,
            "verdict": verdict, "q": bool(q),
        },
    )  # fmt: skip
    s.commit()
    return csv_response("fraudlens-cases", CASE_COLUMNS, out, total)


@router.get("/cases/workload")
def workload(p: Plat, s: Db, ctx: Reviewer) -> dict:
    """Open cases per reviewer: how many, how urgent, how old. Unassigned work is its own row."""
    now = p.scorer.now()
    rows = s.scalars(select(Case).where(Case.status != "closed").order_by(Case.opened_at)).all()
    names = _names(s, [c.assigned_to for c in rows])
    held = {}
    if rows:
        held = dict(
            s.execute(
                select(Decision.case_id, func.sum(Transaction.amount))
                .join(Transaction, Transaction.txn_id == Decision.txn_id)
                .where(Decision.case_id.in_([c.id for c in rows]), Transaction.status == "held")
                .group_by(Decision.case_id)
            ).all()
        )
    board: dict[int | None, dict] = {}
    for c in rows:
        row = board.setdefault(
            c.assigned_to,
            {
                "assignee_id": c.assigned_to,
                "assignee": names.get(c.assigned_to),
                "open": 0, "escalated": 0, "at_risk": 0, "breached": 0,
                "held_amount": 0.0, "oldest_opened_at": c.opened_at, "next_due_at": None,
            },
        )  # fmt: skip
        state = sla_state(c, now)
        row["open"] += 1
        row["escalated"] += c.status == "escalated"
        row["at_risk"] += state == "at_risk"
        row["breached"] += state == "breached"
        row["held_amount"] += float(held.get(c.id, 0))
        if c.sla_due_at is not None and state != "breached":
            due = row["next_due_at"]
            row["next_due_at"] = c.sla_due_at if due is None else min(due, c.sla_due_at)
    people = sorted(board.values(), key=lambda r: (-r["breached"], -r["at_risk"], -r["open"]))
    return {
        "now": now,
        "at_risk_fraction": AT_RISK_FRACTION,
        "totals": {
            key: sum(r[key] for r in people)
            for key in ("open", "escalated", "at_risk", "breached", "held_amount")
        },
        "reviewers": people,
    }


@router.post("/cases/sla-sweep")
def sla_sweep(p: Plat, ctx: Supervisor) -> dict:
    """Record the cases that have missed their review deadline now, instead of waiting
    for the periodic check."""
    return {"breached": workflow.sweep_sla(p.scorer, p.settings.sla_auto_escalate)}


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
    claims = s.scalars(select(Refund).where(Refund.case_id == case_id).order_by(Refund.id)).all()
    people = [case.assigned_to, case.closed_by, *(e.actor_id for e in events)]
    people += [who for f in freezes for who in (f.requested_by, f.decided_by)]
    people += [r.settled_by for r in claims]
    names = _names(s, people)
    now = p.scorer.now()
    return {
        **case_view(case, now, names),
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
        "refunds": {
            "claims": [refund_view(r, now, names) for r in claims],
            # What is still in the wallet: the most a confirmed verdict can return.
            "recoverable": refunds.balance(s, case.subject_id),
            "wallet_frozen": case.subject_id in p.scorer.frozen,
        },
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
    """The human decision: closes the case and releases or blocks the held money.

    Confirmed fraud on a frozen wallet also refunds the victims who reported it; on a
    wallet not frozen yet it requests the freeze, and its approval pays them.
    """
    outcome = workflow.give_verdict(
        p.scorer, ctx, case_id, body.verdict, body.note, body.reason_code
    )
    return {"case": case_view(outcome.pop("case"), p.scorer.now()), **outcome}


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
