"""Refunds for scam victims: freeze the mule wallet, then return what is left in it.

1. A customer reports a completed payment as a scam. Their report opens a refund
   claim for that payment, joined to the case on the receiving wallet.
2. The wallet is frozen with the usual two-person freeze, so nothing more can be
   cashed out or sent on. Until then, the case is the only thing protecting the money.
3. The case verdict settles every claim on it:
   - `confirmed_fraud`: once the wallet is frozen, what is still in it goes back
     to the claimants. If it holds less than they lost, each gets the same share of
     their loss. A confirmed verdict on a wallet that is not frozen yet asks for the
     freeze itself, and the claims are paid the moment a second person approves it.
   - any other verdict: the claims are declined and no money moves.

A reviewer can decline one claim on an open case (the claimant looks like part of
the scheme, not a victim of it). Nobody can pay one by hand: money only leaves a
wallet that two people froze and a person confirmed as fraud.

FraudLens decides; upay's ledger moves the money. A paid claim is that instruction,
and what the victim's app shows as their receipt.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .audit import Ctx, WorkflowError, audit
from .models import Case, CaseEvent, CustomerReport, FreezeRequest, Refund, Transaction
from .scoring import Scorer

# The promise shown to the victim. Five days, as UK payment providers must refund
# authorised-push-payment scams; the clock is the platform's, like every deadline.
SLA = timedelta(days=5)
REFUNDABLE_TYPES = ("SEND_MONEY",)


def _event(s: Session, case_id: int | None, ctx: Ctx | None, kind: str, **data) -> None:
    if case_id is not None:
        s.add(
            CaseEvent(case_id=case_id, actor_id=ctx.user_id if ctx else None, kind=kind, data=data)
        )


def allocate(claimed: list[float], available: float) -> list[float]:
    """What each claim gets back from `available`: all of it when there is enough,
    otherwise the same share of each loss, rounded down to the paisa so the total
    never exceeds what is there."""
    cents = [round(amount * 100) for amount in claimed]
    total, pot = sum(cents), max(int(available * 100 + 1e-6), 0)
    if total <= pot:
        return [c / 100 for c in cents]
    return [c * pot // total / 100 for c in cents]


def balance(s: Session, wallet_id: str) -> float:
    """What is still in the wallet, from the ledger.

    The balance upay last reported for one of the wallet's own payments, plus what
    came in and minus what went out after it, minus refunds already paid from it.
    A wallet that has never paid anything out is the sum of what it received.
    """
    done = Transaction.status == "completed"
    last = s.execute(
        select(
            Transaction.ts,
            Transaction.txn_id,
            Transaction.sender_balance_before,
            Transaction.amount,
        )
        .where(
            Transaction.sender_id == wallet_id,
            done,
            Transaction.sender_balance_before.is_not(None),
        )
        .order_by(Transaction.ts.desc(), Transaction.txn_id.desc())
        .limit(1)
    ).first()
    after, base, refunded_after = [], 0.0, []
    if last is not None:
        ts, txn_id, before, amount = last
        base = max(float(before) - float(amount), 0.0)
        after = [or_(Transaction.ts > ts, (Transaction.ts == ts) & (Transaction.txn_id > txn_id))]
        refunded_after = [Refund.settled_at > ts]

    def total(*where) -> float:
        amount = func.coalesce(func.sum(Transaction.amount), 0)
        return float(s.scalar(select(amount).where(done, *where, *after)))

    received = total(Transaction.receiver_id == wallet_id)
    sent = total(Transaction.sender_id == wallet_id)
    refunded = float(
        s.scalar(
            select(func.coalesce(func.sum(Refund.amount_refunded), 0)).where(
                Refund.wallet_id == wallet_id, Refund.status == "paid", *refunded_after
            )
        )
    )
    return round(max(base + received - sent - refunded, 0.0), 2)


def open_claim(
    s: Session, scorer: Scorer, ctx: Ctx, report: CustomerReport, txn: Transaction | None
) -> Refund | None:
    """The claim a scam report makes for its payment. Only money that actually left
    the customer can be claimed: a payment that was held, cancelled or blocked never
    reached the scammer. Reporting the same payment again returns the first claim."""
    if txn is None or txn.status != "completed" or txn.type not in REFUNDABLE_TYPES:
        return None
    existing = s.scalar(select(Refund).where(Refund.txn_id == txn.txn_id))
    if existing is not None:
        return existing
    now = scorer.now()
    refund = Refund(
        txn_id=txn.txn_id,
        victim_id=txn.sender_id,
        wallet_id=txn.receiver_id,
        case_id=report.case_id,
        report_id=report.id,
        amount_claimed=txn.amount,
        filed_at=now,
        sla_due_at=now + SLA,
    )
    s.add(refund)
    s.flush()
    _event(
        s, report.case_id, None, "refund_claimed",
        refund_id=refund.id, txn_id=txn.txn_id, amount=txn.amount, victim_id=txn.sender_id,
    )  # fmt: skip
    audit(
        s, ctx, "refund.claim", "refund", refund.id,
        txn_id=txn.txn_id, wallet_id=txn.receiver_id, amount=txn.amount,
    )  # fmt: skip
    return refund


def _close(s: Session, ctx: Ctx, refund: Refund, status: str, outcome: str, now, amount=0.0):
    refund.status, refund.outcome, refund.amount_refunded = status, outcome, amount
    refund.settled_at, refund.settled_by = now, ctx.user_id
    _event(
        s, refund.case_id, ctx, f"refund_{status}",
        refund_id=refund.id, txn_id=refund.txn_id, amount=amount, outcome=outcome,
    )  # fmt: skip
    audit(
        s, ctx, f"refund.{status}", "refund", refund.id,
        txn_id=refund.txn_id, wallet_id=refund.wallet_id, victim_id=refund.victim_id,
        claimed=refund.amount_claimed, refunded=amount, outcome=outcome,
    )  # fmt: skip


def settle(s: Session, scorer: Scorer, ctx: Ctx, wallet_id: str) -> list[Refund]:
    """Pay the open claims on a frozen wallet whose case confirmed fraud. Called
    inside `scorer.transaction()` by the verdict and by the freeze approval, so
    whichever of the two comes second pays. Returns the claims it settled."""
    if wallet_id not in scorer.frozen:
        return []
    claims = s.scalars(
        select(Refund)
        .join(Case, Case.id == Refund.case_id)
        .where(
            Refund.wallet_id == wallet_id,
            Refund.status == "open",
            Case.verdict == "confirmed_fraud",
        )
        .order_by(Refund.filed_at, Refund.id)
        .with_for_update(of=Refund)
    ).all()
    if not claims:
        return []
    now = scorer.now()
    amounts = allocate([c.amount_claimed for c in claims], balance(s, wallet_id))
    for claim, amount in zip(claims, amounts, strict=True):
        if amount > 0:
            _close(s, ctx, claim, "paid", "confirmed_fraud", now, amount)
        else:
            _close(s, ctx, claim, "unrecoverable", "nothing_left", now)
    s.flush()
    return claims


def on_verdict(s: Session, scorer: Scorer, ctx: Ctx, case: Case) -> dict:
    """What a closed case means for its claims. Returns what happened, for the response."""
    claims = s.scalars(
        select(Refund).where(Refund.case_id == case.id, Refund.status == "open")
    ).all()
    out = {
        "refunds_paid": [],
        "refunds_waiting": [],
        "refunds_declined": [],
        "freeze_request": None,
    }
    if not claims:
        return out
    if case.verdict != "confirmed_fraud":
        now = scorer.now()
        for claim in claims:
            _close(s, ctx, claim, "declined", "not_confirmed", now)
        out["refunds_declined"] = [c.id for c in claims]
        return out
    settled = settle(s, scorer, ctx, case.subject_id)
    if settled:
        out["refunds_paid"] = [c.id for c in settled if c.status == "paid"]
        return out
    # Not frozen yet: ask for it now, so one approval both freezes and refunds.
    out["refunds_waiting"] = [c.id for c in claims]
    pending = s.scalar(
        select(FreezeRequest).where(
            FreezeRequest.wallet_id == case.subject_id, FreezeRequest.status == "pending"
        )
    )
    if pending is None:
        pending = FreezeRequest(
            wallet_id=case.subject_id,
            case_id=case.id,
            reason=(
                f"Confirmed fraud on case #{case.id}. Freeze so {len(claims)} reported "
                "payment(s) can be refunded from what is left in the wallet."
            ),
            requested_by=ctx.user_id,
        )
        s.add(pending)
        s.flush()
        _event(s, case.id, ctx, "freeze_requested", request_id=pending.id, for_refunds=True)
        audit(
            s, ctx, "freeze.request", "wallet", case.subject_id,
            request_id=pending.id, case_id=case.id, for_refunds=True,
        )  # fmt: skip
    out["freeze_request"] = pending.id
    return out


def decline(scorer: Scorer, ctx: Ctx, refund_id: int, note: str) -> Refund:
    """A reviewer takes one claim out: the claimant is not a victim. No money moves."""
    with scorer.transaction() as s:
        refund = s.scalar(select(Refund).where(Refund.id == refund_id).with_for_update())
        if refund is None:
            raise WorkflowError(404, "refund_not_found", f"no refund claim {refund_id}")
        if refund.status != "open":
            raise WorkflowError(409, "already_settled", f"the claim is already {refund.status}")
        _close(s, ctx, refund, "declined", "not_a_victim", scorer.now())
        refund.note = note
    return refund
