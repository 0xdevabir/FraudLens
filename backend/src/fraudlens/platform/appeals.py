"""Customer appeals: "this is a genuine payment", answered by a named person.

A customer whose payment was warned, put through a step-up or held can say it
is genuine, with how they know the recipient and why. An analyst or supervisor
answers within a deadline:

- Approving a held payment releases it, through the same path a `legitimate`
  verdict uses. Money is never released into a wallet that is confirmed fraud
  or frozen, whatever the appeal says.
- Rejecting keeps the hold; the case still decides.
- An appeal against a warning or a step-up moves no money: the customer
  already decides those. It records that the warning was wrong, and an approved
  one becomes a `legitimate` label (mlops/feedback.py). It never shortens a
  cooling-off period: "let it through now, it is genuine" is exactly what a
  scammer coaching a victim would have them say.

What the customer typed is shown masked and is never read to decide anything.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .audit import Ctx, WorkflowError, audit
from .cases import _event
from .models import Appeal, Case, Decision, Transaction
from .scoring import Scorer

APPEALABLE = ("warn", "step_up", "hold")
# A warning or step-up appeal is feedback: nothing is waiting on it, so a day.
FEEDBACK_SLA_MINUTES = 24 * 60


def sla_minutes(scorer: Scorer, tier: str) -> int:
    if tier == "hold":
        return scorer.policy.tiers["hold"].review_sla_minutes or FEEDBACK_SLA_MINUTES
    return FEEDBACK_SLA_MINUTES


def file_appeal(
    scorer: Scorer, ctx: Ctx, wallet_id: str, txn_id: int, relation: str, reason: str
) -> Appeal:
    with scorer.transaction() as s:
        txn = s.scalar(
            select(Transaction).where(Transaction.txn_id == txn_id).with_for_update(key_share=True)
        )
        # The same answer for "no such transaction" and "not yours".
        if txn is None or txn.sender_id != wallet_id:
            raise WorkflowError(404, "txn_not_found", "no such transaction for this wallet")
        decision = s.get(Decision, txn_id)
        if decision is None or decision.tier not in APPEALABLE:
            raise WorkflowError(
                409, "not_appealable", "only a warned or held payment can be appealed"
            )
        if decision.tier == "hold" and txn.status != "held":
            raise WorkflowError(409, "already_decided", f"the payment is already {txn.status}")
        now = scorer.now()
        appeal = Appeal(
            txn_id=txn_id,
            wallet_id=wallet_id,
            case_id=decision.case_id,
            tier=decision.tier,
            relation=relation,
            reason=reason,
            filed_at=now,
            sla_due_at=now + timedelta(minutes=sla_minutes(scorer, decision.tier)),
        )
        s.add(appeal)
        try:
            s.flush()
        except IntegrityError:
            raise WorkflowError(
                409, "appeal_exists", "this payment has already been appealed"
            ) from None
        if appeal.case_id is not None:
            _event(
                s, appeal.case_id, None, "appeal_filed",
                appeal_id=appeal.id, txn_id=txn_id, relation=relation, tier=decision.tier,
            )  # fmt: skip
        audit(
            s, ctx, "appeal.file", "appeal", appeal.id,
            txn_id=txn_id, wallet_id=wallet_id, tier=decision.tier, relation=relation,
        )  # fmt: skip
    return appeal


def decide_appeal(scorer: Scorer, ctx: Ctx, appeal_id: int, approve: bool, note: str) -> dict:
    """The person's answer. Approving releases a payment that is still held."""
    with scorer.transaction() as s:
        appeal = s.scalar(select(Appeal).where(Appeal.id == appeal_id).with_for_update())
        if appeal is None:
            raise WorkflowError(404, "appeal_not_found", f"no appeal {appeal_id}")
        if appeal.status != "pending":
            raise WorkflowError(409, "already_decided", f"the appeal was {appeal.status}")
        if appeal.case_id is not None and ctx.role != "supervisor":
            case = s.get(Case, appeal.case_id)
            if case is not None and case.status == "escalated":
                raise WorkflowError(403, "needs_supervisor", "an escalated case needs a supervisor")
        txn = s.scalar(
            select(Transaction)
            .where(Transaction.txn_id == appeal.txn_id)
            .with_for_update(key_share=True)
        )
        released = False
        if approve and txn.status == "held":
            if txn.receiver_id in scorer.engine.flagged:
                raise WorkflowError(
                    409, "recipient_confirmed_fraud",
                    "the receiving wallet is confirmed fraud: an appeal cannot release money to it",
                )  # fmt: skip
            scorer.complete(txn)  # a frozen party turns this into `rejected`
            released = txn.status == "completed"
        now = scorer.now()
        appeal.status = "approved" if approve else "rejected"
        appeal.decided_by, appeal.decision_note, appeal.decided_at = ctx.user_id, note, now
        if appeal.case_id is not None:
            _event(
                s, appeal.case_id, ctx, f"appeal_{appeal.status}", note,
                appeal_id=appeal.id, txn_id=appeal.txn_id, released=released,
            )  # fmt: skip
        audit(
            s, ctx, f"appeal.{'approve' if approve else 'reject'}", "appeal", appeal.id,
            txn_id=appeal.txn_id, released=released, txn_status=txn.status,
            within_sla=now <= appeal.sla_due_at,
        )  # fmt: skip
        s.flush()
    return {"appeal": appeal, "txn_status": txn.status, "released": released}
