"""Case workflow: every consequential action is taken by a named person and audited.

The scorer pauses a payment at most. What happens next is decided here, by
people: an analyst releases or blocks held money with a verdict, and freezing a
wallet needs a second person to approve the first one's request.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import blocklist as lists
from . import notify, tracking
from .audit import Ctx, WorkflowError, audit
from .models import (
    Case,
    CaseEvent,
    CustomerReport,
    Decision,
    FreezeRequest,
    Transaction,
    User,
    Wallet,
)
from .scoring import Scorer

REVIEWERS = ("analyst", "supervisor")

# A case is "at risk" once this share of its review window is left, or less.
AT_RISK_FRACTION = 0.3
SYSTEM = Ctx(None, "system", None)


def sla_state(case: Case, now: datetime) -> str | None:
    """`ok`, `at_risk` or `breached` for an open case with a review deadline; else None."""
    if case.status == "closed" or case.sla_due_at is None:
        return None
    if case.sla_due_at < now:
        return "breached"
    window = (case.sla_due_at - case.opened_at).total_seconds()
    left = (case.sla_due_at - now).total_seconds()
    return "at_risk" if left <= window * AT_RISK_FRACTION else "ok"


def sweep_sla(scorer: Scorer, auto_escalate: bool = False) -> list[int]:
    """Record the cases that have missed their review deadline, once each.

    A breach is written to the timeline and the audit log. With `auto_escalate`
    the case also goes to the supervisors; the money stays held either way, and
    only a person releases or blocks it. Returns the ids newly found breached.
    """
    now = scorer.now()
    found: list[int] = []
    with scorer.sessions() as s:
        already = select(CaseEvent.case_id).where(CaseEvent.kind == "sla_breached")
        due = s.scalars(
            select(Case)
            .where(
                Case.status != "closed",
                Case.sla_due_at.is_not(None),
                Case.sla_due_at < now,
                Case.id.not_in(already),
            )
            .order_by(Case.sla_due_at)
            .with_for_update(skip_locked=True)
        ).all()
        for case in due:
            minutes = round((now - case.sla_due_at).total_seconds() / 60)
            escalate_it = auto_escalate and case.status in ("open", "in_review")
            _event(
                s, case.id, SYSTEM, "sla_breached",
                f"review deadline missed by {minutes} min",
                due_at=case.sla_due_at.isoformat(), escalated=escalate_it,
            )  # fmt: skip
            audit(s, SYSTEM, "case.sla_breached", "case", case.id, minutes_late=minutes)
            if escalate_it:
                case.status = "escalated"
                _event(s, case.id, SYSTEM, "escalated", "automatic: review deadline missed")
                audit(s, SYSTEM, "case.escalate", "case", case.id, automatic=True)
            found.append(case.id)
        s.commit()
    return found


def _case(s: Session, case_id: int, lock: bool = True) -> Case:
    query = select(Case).where(Case.id == case_id)
    if lock:
        # NO KEY UPDATE: two people cannot act on a case at once, timeline inserts still can.
        query = query.with_for_update(key_share=True)
    case = s.scalar(query)
    if case is None:
        raise WorkflowError(404, "case_not_found", f"no case {case_id}")
    return case


def _wallet(s: Session, wallet_id: str, lock: bool = False) -> Wallet:
    query = select(Wallet).where(Wallet.wallet_id == wallet_id)
    wallet = s.scalar(query.with_for_update(key_share=True) if lock else query)
    if wallet is None:
        raise WorkflowError(404, "wallet_not_found", f"no wallet {wallet_id}")
    return wallet


def _event(s: Session, case_id: int, ctx: Ctx | None, kind: str, body: str | None = None, **data):
    actor = ctx.user_id if ctx else None
    s.add(CaseEvent(case_id=case_id, actor_id=actor, kind=kind, body=body, data=data))


# ------------------------------------------------------------------ cases


def open_case(scorer: Scorer, ctx: Ctx, wallet_id: str, reason: str) -> Case:
    """An analyst opens an investigation on a wallet without waiting for an alert."""
    with scorer.transaction() as s:
        _wallet(s, wallet_id)
        existing = s.scalar(
            select(Case.id).where(Case.subject_id == wallet_id, Case.status != "closed")
        )
        if existing is not None:
            raise WorkflowError(
                409, "case_exists", "this wallet already has an open case", case_id=existing
            )
        case = Case(subject_id=wallet_id, priority="warn", source="manual", opened_at=scorer.now())
        s.add(case)
        s.flush()
        _event(s, case.id, ctx, "opened", reason)
        audit(s, ctx, "case.open", "case", case.id, wallet_id=wallet_id)
    return case


def assign(sessions, ctx: Ctx, case_id: int, assignee_id: int) -> Case:
    with sessions() as s:
        case = _case(s, case_id)
        if case.status == "closed":
            raise WorkflowError(409, "case_closed", "the case is closed")
        if ctx.role != "supervisor" and assignee_id != ctx.user_id:
            raise WorkflowError(
                403, "forbidden", "analysts can take a case, supervisors assign them"
            )
        assignee = s.get(User, assignee_id)
        if assignee is None or not assignee.is_active or assignee.role not in REVIEWERS:
            raise WorkflowError(422, "invalid_assignee", "cases go to an active analyst")
        case.assigned_to = assignee_id
        if case.status == "open":
            case.status = "in_review"
        _event(s, case.id, ctx, "assigned", assignee_id=assignee_id, assignee=assignee.username)
        audit(s, ctx, "case.assign", "case", case.id, assignee=assignee.username)
        s.commit()
    return case


def add_note(sessions, ctx: Ctx, case_id: int, body: str) -> CaseEvent:
    with sessions() as s:
        case = _case(s, case_id, lock=False)
        event = CaseEvent(case_id=case.id, actor_id=ctx.user_id, kind="note", body=body)
        s.add(event)
        audit(s, ctx, "case.note", "case", case.id)
        s.commit()
    return event


def escalate(sessions, ctx: Ctx, case_id: int, reason: str) -> Case:
    with sessions() as s:
        case = _case(s, case_id)
        if case.status in ("closed", "escalated"):
            raise WorkflowError(409, f"case_{case.status}", f"the case is already {case.status}")
        case.status = "escalated"
        _event(s, case.id, ctx, "escalated", reason)
        audit(s, ctx, "case.escalate", "case", case.id)
        s.commit()
    return case


def give_verdict(
    scorer: Scorer,
    ctx: Ctx,
    case_id: int,
    verdict: str,
    note: str,
    reason_code: str | None = None,
) -> dict:
    """Close a case. This is where held money is released or blocked, by a person."""
    if reason_code is not None and verdict != "legitimate":
        raise WorkflowError(
            422, "reason_code_needs_legitimate", "a reason code explains a legitimate verdict"
        )
    with scorer.transaction() as s:
        case = _case(s, case_id)
        if case.status == "closed":
            raise WorkflowError(409, "case_closed", "the case already has a verdict")
        if ctx.role != "supervisor":
            if case.status == "escalated":
                raise WorkflowError(403, "needs_supervisor", "an escalated case needs a supervisor")
            if case.assigned_to not in (None, ctx.user_id):
                raise WorkflowError(403, "not_assignee", "the case is assigned to someone else")
        if case.assigned_to is None:
            case.assigned_to = ctx.user_id

        fraud = verdict == "confirmed_fraud"
        waiting = s.scalars(
            select(Transaction)
            .join(Decision, Decision.txn_id == Transaction.txn_id)
            .where(
                Decision.case_id == case.id,
                Transaction.status.in_(("held", "pending_customer")),
            )
            .order_by(Transaction.ts, Transaction.txn_id)
            .with_for_update(of=Transaction, key_share=True)
        ).all()
        released, blocked = [], []
        for txn in waiting:
            if fraud:
                # Also stops payments the customer has not confirmed yet.
                txn.status, txn.status_reason = "blocked", "confirmed_fraud"
                blocked.append(txn.txn_id)
            elif txn.status == "held":
                scorer.complete(txn)
                released.append(txn.txn_id)
        now = scorer.now()
        case.status, case.verdict, case.reason_code = "closed", verdict, reason_code
        case.closed_at, case.closed_by = now, ctx.user_id
        _event(
            s, case.id, ctx, "verdict", note,
            verdict=verdict, reason_code=reason_code, released=released, blocked=blocked,
        )  # fmt: skip
        audit(
            s, ctx, "case.verdict", "case", case.id,
            verdict=verdict, reason_code=reason_code, subject=case.subject_id,
            released=released, blocked=blocked,
        )  # fmt: skip
        notify.enqueue(
            s, "case.verdict",
            {"case_id": case.id, "verdict": verdict, "released": released, "blocked": blocked},
        )  # fmt: skip
        s.flush()
        if fraud:
            scorer.flag(s, case.subject_id, now.timestamp(), "case_verdict", "case", case.id)
            # Also listed, so the same wallet is recognised if it comes back unflagged.
            lists.add(
                s, ctx, "wallet", case.subject_id, f"confirmed fraud in case #{case.id}",
                "verdict", case_id=case.id,
            )  # fmt: skip
            scorer.load_blocklist(s)
    return {"case": case, "released": released, "blocked": blocked}


# ----------------------------------------------------------------- freezes


def request_freeze(
    sessions, ctx: Ctx, wallet_id: str, reason: str, case_id: int | None = None
) -> FreezeRequest:
    with sessions() as s:
        wallet = _wallet(s, wallet_id)
        if wallet.status == "frozen":
            raise WorkflowError(409, "already_frozen", "the wallet is already frozen")
        if case_id is not None and _case(s, case_id, lock=False).subject_id != wallet_id:
            raise WorkflowError(422, "case_mismatch", "that case is about another wallet")
        request = FreezeRequest(
            wallet_id=wallet_id, case_id=case_id, reason=reason, requested_by=ctx.user_id
        )
        s.add(request)
        try:
            s.flush()
        except IntegrityError:
            raise WorkflowError(
                409, "freeze_pending", "a freeze request for this wallet is already waiting"
            ) from None
        if case_id is not None:
            _event(s, case_id, ctx, "freeze_requested", reason, request_id=request.id)
        audit(s, ctx, "freeze.request", "wallet", wallet_id, request_id=request.id, case_id=case_id)
        s.commit()
    return request


def decide_freeze(scorer: Scorer, ctx: Ctx, request_id: int, approve: bool, note: str):
    """The second person. Whoever asked for the freeze cannot approve it."""
    with scorer.transaction() as s:
        request = s.scalar(
            select(FreezeRequest).where(FreezeRequest.id == request_id).with_for_update()
        )
        if request is None:
            raise WorkflowError(404, "request_not_found", f"no freeze request {request_id}")
        if request.status != "pending":
            raise WorkflowError(409, "already_decided", f"the request was {request.status}")
        if request.requested_by == ctx.user_id:
            raise WorkflowError(
                403, "two_person_rule", "a freeze must be approved by a second person"
            )
        wallet = _wallet(s, request.wallet_id, lock=True)
        now = scorer.now()
        request.status = "approved" if approve else "rejected"
        request.decided_by, request.decision_note, request.decided_at = ctx.user_id, note, now
        if approve:
            wallet.status, wallet.frozen_at = "frozen", now
        if request.case_id is not None:
            _event(s, request.case_id, ctx, f"freeze_{request.status}", note, request_id=request.id)
        audit(
            s, ctx, f"freeze.{'approve' if approve else 'reject'}", "wallet", wallet.wallet_id,
            request_id=request.id, requested_by=request.requested_by,
        )  # fmt: skip
        if approve:
            notify.enqueue(
                s, "freeze.approved",
                {
                    "wallet_id": wallet.wallet_id,
                    "request_id": request.id,
                    "case_id": request.case_id,
                },
            )  # fmt: skip
        s.flush()
        if approve:
            scorer.frozen.add(wallet.wallet_id)
    return request


def unfreeze(scorer: Scorer, ctx: Ctx, wallet_id: str, reason: str) -> Wallet:
    with scorer.transaction() as s:
        wallet = _wallet(s, wallet_id, lock=True)
        if wallet.status != "frozen":
            raise WorkflowError(409, "not_frozen", "the wallet is not frozen")
        wallet.status, wallet.frozen_at = "active", None
        audit(s, ctx, "freeze.lift", "wallet", wallet_id, reason=reason)
        s.flush()
        scorer.frozen.discard(wallet_id)
    return wallet


# --------------------------------------------------------------- customers


def customer_respond(
    scorer: Scorer, ctx: Ctx, wallet_id: str, txn_id: int, action: str, step_up_passed: bool
) -> Transaction:
    """The customer's answer to a warning or a step-up: go ahead, or cancel."""
    with scorer.transaction() as s:
        txn = s.scalar(
            select(Transaction).where(Transaction.txn_id == txn_id).with_for_update(key_share=True)
        )
        # The same answer for "no such transaction" and "not yours".
        if txn is None or txn.sender_id != wallet_id:
            raise WorkflowError(404, "txn_not_found", "no such transaction for this wallet")
        if txn.status != "pending_customer":
            raise WorkflowError(409, "not_pending", f"the transaction is {txn.status}")
        decision = s.get(Decision, txn_id)
        now = scorer.now()
        if action == "proceed" and decision.tier == "step_up":
            if not step_up_passed:
                raise WorkflowError(403, "step_up_required", "the customer must verify again")
            wait = scorer.policy.tiers["step_up"].cooling_off_minutes or 0
            remaining = (txn.ts + timedelta(minutes=wait) - now).total_seconds()
            if remaining > 0:
                raise WorkflowError(
                    409, "cooling_off", "the cooling-off period has not passed",
                    retry_after_seconds=math.ceil(remaining),
                )  # fmt: skip
        decision.customer_response, decision.responded_at = action, now
        if decision.case_id is not None:
            _event(s, decision.case_id, None, "customer_response", txn_id=txn_id, action=action)
        audit(s, ctx, f"customer.{action}", "transaction", txn_id, wallet_id=wallet_id)
        s.flush()
        if action == "cancel":
            txn.status, txn.status_reason = "cancelled", "customer_cancelled"
        else:
            scorer.complete(txn)
    return txn


def report_scam(
    scorer: Scorer,
    ctx: Ctx,
    reporter_id: str,
    reported_wallet_id: str,
    txn_id: int | None,
    category: str,
    description: str,
) -> CustomerReport:
    """A customer says they were scammed. It opens a case; a person decides what it means."""
    with scorer.transaction() as s:
        _wallet(s, reporter_id)
        if s.get(Wallet, reported_wallet_id) is None:
            raise WorkflowError(422, "unknown_wallet", "the reported wallet does not exist")
        if txn_id is not None:
            txn = s.get(Transaction, txn_id)
            if txn is None or (txn.sender_id, txn.receiver_id) != (reporter_id, reported_wallet_id):
                raise WorkflowError(
                    422, "txn_mismatch", "that transaction is not between these two wallets"
                )
        now = scorer.now()
        case = s.scalar(
            select(Case).where(Case.subject_id == reported_wallet_id, Case.status != "closed")
        )
        if case is None:
            case = Case(
                subject_id=reported_wallet_id,
                priority="warn",
                source="customer_report",
                opened_at=now,
            )
            s.add(case)
            s.flush()
        report = CustomerReport(
            reference=tracking.new_reference(s),
            reporter_id=reporter_id,
            reported_wallet_id=reported_wallet_id,
            txn_id=txn_id,
            category=category,
            description=description,
            case_id=case.id,
            reported_at=now,
        )
        s.add(report)
        s.flush()
        _event(
            s, case.id, None, "customer_report", description,
            report_id=report.id, reporter_id=reporter_id, category=category, txn_id=txn_id,
        )  # fmt: skip
        audit(s, ctx, "customer.report", "wallet", reported_wallet_id, report_id=report.id)
    return report
