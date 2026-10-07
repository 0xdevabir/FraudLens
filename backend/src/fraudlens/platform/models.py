"""Tables.

Ground-truth columns of the synthetic dataset (is_fraud, is_mule, typology of a
live transaction, ...) have no column here on purpose: the platform only ever
knows what a real one would know.

Two kinds of time are stored. Domain time (`ts`, `opened_at`, `flagged_at`, ...)
is the time on the event stream, which during a replay is the recorded time of
the transaction. `created_at` is always the wall clock of the server.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ROLES = ("analyst", "supervisor", "admin", "service")
TXN_STATUS = ("completed", "pending_customer", "held", "cancelled", "blocked", "rejected")
TXN_SOURCE = ("history", "replay", "live")
CASE_STATUS = ("open", "in_review", "escalated", "closed")
VERDICTS = ("confirmed_fraud", "legitimate", "inconclusive")
CASE_SOURCE = ("alert", "customer_report", "manual")
FREEZE_STATUS = ("pending", "approved", "rejected")
APPEAL_STATUS = ("pending", "approved", "rejected")
# open: the case is still investigating; paid: upay returned money (maybe less than lost);
# unrecoverable: fraud confirmed, nothing left in the wallet; declined: no refund.
REFUND_STATUS = ("open", "paid", "unrecoverable", "declined")
# How the customer says they know the person they are paying.
APPEAL_RELATIONS = (
    "family",
    "friend",
    "business",
    "seller",
    "landlord",
    "employer",
    "other",
    "none",
)


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


Timestamp = DateTime(timezone=True)
Money = Numeric(14, 2, asdecimal=False)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint(_one_of("role", ROLES), name="role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())


class Wallet(Base):
    __tablename__ = "wallets"
    __table_args__ = (CheckConstraint("status IN ('active', 'frozen')", name="status"),)

    wallet_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(Timestamp)
    district: Mapped[str] = mapped_column(String(40))
    area_type: Mapped[str] = mapped_column(String(16))
    channel: Mapped[str] = mapped_column(String(16))
    segment: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), server_default="active")
    frozen_at: Mapped[datetime | None] = mapped_column(Timestamp)


class Agent(Base):
    __tablename__ = "agents"

    agent_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    district: Mapped[str] = mapped_column(String(40))


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint(_one_of("status", TXN_STATUS), name="status"),
        CheckConstraint(_one_of("source", TXN_SOURCE), name="source"),
        CheckConstraint("amount > 0", name="amount_positive"),
        Index("ix_transactions_sender_ts", "sender_id", "ts"),
        Index("ix_transactions_receiver_ts", "receiver_id", "ts"),
        # What the scorer re-applies to rebuild its state after a restart.
        Index(
            "ix_transactions_applied",
            "applied_seq",
            postgresql_where=text("applied_seq IS NOT NULL"),
        ),
    )

    txn_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    ts: Mapped[datetime] = mapped_column(Timestamp)
    type: Mapped[str] = mapped_column(String(16))
    sender_id: Mapped[str] = mapped_column(String(32))
    sender_type: Mapped[str] = mapped_column(String(16))
    receiver_id: Mapped[str] = mapped_column(String(32))
    receiver_type: Mapped[str] = mapped_column(String(16))
    amount: Mapped[float] = mapped_column(Money)
    sender_balance_before: Mapped[float | None] = mapped_column(Money)
    device_id: Mapped[str] = mapped_column(String(64), server_default="")
    channel: Mapped[str] = mapped_column(String(16))
    district: Mapped[str] = mapped_column(String(40))
    # Network prefix of the request's address (never the address); "" when not sent.
    network: Mapped[str] = mapped_column(String(48), server_default="")
    status: Mapped[str] = mapped_column(String(20))
    status_reason: Mapped[str | None] = mapped_column(String(40))
    source: Mapped[str] = mapped_column(String(8))
    # Domain time at which the transaction was applied to the scorer's state; NULL
    # while it is waiting for the customer or an analyst, or if it never went through.
    applied_at: Mapped[datetime | None] = mapped_column(Timestamp)
    # Position in the scorer's log: the order in which state changes were applied.
    applied_seq: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())


class Case(Base):
    __tablename__ = "cases"
    __table_args__ = (
        CheckConstraint(_one_of("status", CASE_STATUS), name="status"),
        CheckConstraint(_one_of("source", CASE_SOURCE), name="source"),
        CheckConstraint(f"verdict IS NULL OR {_one_of('verdict', VERDICTS)}", name="verdict"),
        CheckConstraint("(status = 'closed') = (verdict IS NOT NULL)", name="closed_has_verdict"),
        # One open investigation per wallet: new alerts on it join the same case.
        Index(
            "uq_cases_open_subject",
            "subject_id",
            unique=True,
            postgresql_where=text("status <> 'closed'"),
        ),
        Index("ix_cases_status_sla", "status", "sla_due_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[str] = mapped_column(String(32))  # the wallet under investigation
    status: Mapped[str] = mapped_column(String(16), server_default="open")
    priority: Mapped[str] = mapped_column(String(8))  # highest tier among its alerts
    source: Mapped[str] = mapped_column(String(20))
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    opened_at: Mapped[datetime] = mapped_column(Timestamp)
    sla_due_at: Mapped[datetime | None] = mapped_column(Timestamp)
    verdict: Mapped[str | None] = mapped_column(String(20))
    closed_at: Mapped[datetime | None] = mapped_column(Timestamp)
    closed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())


class Decision(Base):
    __tablename__ = "decisions"
    __table_args__ = (
        CheckConstraint("tier IN ('allow', 'warn', 'step_up', 'hold')", name="tier"),
        CheckConstraint(
            "customer_response IS NULL OR customer_response IN ('proceed', 'cancel')",
            name="customer_response",
        ),
        Index(
            "ix_decisions_alerts",
            text("decided_at DESC"),
            postgresql_where=text("tier <> 'allow'"),
        ),
        Index("ix_decisions_case", "case_id", postgresql_where=text("case_id IS NOT NULL")),
    )

    txn_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("transactions.txn_id", ondelete="CASCADE"), primary_key=True
    )
    tier: Mapped[str] = mapped_column(String(8))
    action: Mapped[str] = mapped_column(String(20))
    requires_review: Mapped[bool] = mapped_column(Boolean)
    mode: Mapped[str] = mapped_column(String(12))
    decided_by: Mapped[str] = mapped_column(String(64))
    model_tier: Mapped[str | None] = mapped_column(String(8))
    risk: Mapped[float | None] = mapped_column(Float(53))
    risk_score: Mapped[int | None] = mapped_column(Integer)
    risk_band: Mapped[str] = mapped_column(String(16))
    scores: Mapped[dict] = mapped_column(JSONB)
    model_version: Mapped[str | None] = mapped_column(String(16))
    policy_version: Mapped[str] = mapped_column(String(16))
    scenario: Mapped[str | None] = mapped_column(String(16))
    # Rule trace for every decision; reasons, evidence, actions and similar cases for alerts.
    detail: Mapped[dict] = mapped_column(JSONB)
    narrative: Mapped[str | None] = mapped_column(Text)
    # The exact model inputs, kept for audit, retraining and drift checks.
    features: Mapped[list[float]] = mapped_column(ARRAY(Float(53)))
    latency_ms: Mapped[float] = mapped_column(Float)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"))
    customer_response: Mapped[str | None] = mapped_column(String(8))
    responded_at: Mapped[datetime | None] = mapped_column(Timestamp)
    decided_at: Mapped[datetime] = mapped_column(Timestamp)
    created_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())


class ShadowScore(Base):
    """What a challenger model would have said about a decision. Never acted on."""

    __tablename__ = "shadow_scores"
    __table_args__ = (
        CheckConstraint("tier IN ('allow', 'warn', 'step_up', 'hold')", name="tier"),
        Index("ix_shadow_scores_model_version", "model_version"),
    )

    txn_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("decisions.txn_id", ondelete="CASCADE"), primary_key=True
    )
    model_version: Mapped[str] = mapped_column(String(16), primary_key=True)
    risk: Mapped[float] = mapped_column(Float(53))
    tier: Mapped[str] = mapped_column(String(8))  # from the challenger's own thresholds
    latency_ms: Mapped[float | None] = mapped_column(Float)  # NULL when scored afterwards
    created_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())


class PendingDecision(Base):
    """A transaction that is in the state and still waits for its decision.

    A stream worker applies recorded events in order under the scorer's lock and
    decides them after releasing it (PLATFORM §8). The feature vector is kept here,
    committed with the transaction, so whichever worker finishes the work makes the
    same decision; the row is deleted in the commit that writes the decision.
    """

    __tablename__ = "pending_decisions"

    txn_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("transactions.txn_id", ondelete="CASCADE"), primary_key=True
    )
    features: Mapped[list[float]] = mapped_column(ARRAY(Float(53)))
    context: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())


class CaseEvent(Base):
    """The case timeline: who did what, in order. Rows are never changed."""

    __tablename__ = "case_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))  # NULL: the system
    kind: Mapped[str] = mapped_column(String(24))
    body: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())


class FreezeRequest(Base):
    __tablename__ = "freeze_requests"
    __table_args__ = (
        CheckConstraint(_one_of("status", FREEZE_STATUS), name="status"),
        # Two-person rule, enforced by the database as well as the API.
        CheckConstraint("decided_by IS NULL OR decided_by <> requested_by", name="two_person"),
        CheckConstraint("(status = 'pending') = (decided_by IS NULL)", name="decided_has_decider"),
        Index(
            "uq_freeze_requests_pending_wallet",
            "wallet_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    wallet_id: Mapped[str] = mapped_column(ForeignKey("wallets.wallet_id"))
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), server_default="pending")
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decision_note: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(Timestamp)
    created_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())


class WalletFlag(Base):
    """A wallet confirmed as fraud by an investigation (ours or an upstream one)."""

    __tablename__ = "wallet_flags"
    __table_args__ = (
        CheckConstraint("source IN ('history', 'replay', 'upstream', 'case')", name="source"),
        Index("ix_wallet_flags_flagged_at", "flagged_at"),
    )

    wallet_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    flagged_at: Mapped[datetime] = mapped_column(Timestamp)
    reason: Mapped[str] = mapped_column(String(40))
    source: Mapped[str] = mapped_column(String(12))
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"))
    applied_seq: Mapped[int | None] = mapped_column(BigInteger)  # see Transaction.applied_seq
    created_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())


class CustomerReport(Base):
    __tablename__ = "customer_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    reporter_id: Mapped[str] = mapped_column(String(32), index=True)
    reported_wallet_id: Mapped[str] = mapped_column(String(32), index=True)
    txn_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("transactions.txn_id"))
    category: Mapped[str] = mapped_column(String(24))
    # Free text typed by a customer: shown to analysts, never used to decide anything.
    description: Mapped[str] = mapped_column(Text)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"))
    reported_at: Mapped[datetime] = mapped_column(Timestamp)
    created_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())


class Appeal(Base):
    """A customer says a warned or held payment is genuine. A person answers it."""

    __tablename__ = "appeals"
    __table_args__ = (
        CheckConstraint(_one_of("status", APPEAL_STATUS), name="status"),
        CheckConstraint(_one_of("relation", APPEAL_RELATIONS), name="relation"),
        CheckConstraint("tier IN ('warn', 'step_up', 'hold')", name="tier"),
        CheckConstraint("(status = 'pending') = (decided_by IS NULL)", name="decided_has_decider"),
        Index("ix_appeals_status_sla", "status", "sla_due_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # One appeal per payment.
    txn_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("transactions.txn_id", ondelete="CASCADE"), unique=True
    )
    wallet_id: Mapped[str] = mapped_column(String(32), index=True)  # the sender, who appeals
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"))
    tier: Mapped[str] = mapped_column(String(8))  # the decision's tier when it was appealed
    relation: Mapped[str] = mapped_column(String(16))
    # Free text typed by a customer: shown masked, never used to decide anything.
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), server_default="pending")
    filed_at: Mapped[datetime] = mapped_column(Timestamp)  # domain time, like opened_at
    sla_due_at: Mapped[datetime] = mapped_column(Timestamp)
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decision_note: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(Timestamp)
    created_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())


class Refund(Base):
    """A scam victim's claim to get back a payment they sent to a mule wallet.

    Opened when the victim reports a completed payment. Settled by the case verdict:
    once the wallet is confirmed fraud and frozen, what is still in it is returned to
    the victims who claimed, shared in proportion to what each lost. upay's ledger
    moves the money; this row is the instruction and the customer's receipt.
    """

    __tablename__ = "refunds"
    __table_args__ = (
        CheckConstraint(_one_of("status", REFUND_STATUS), name="status"),
        CheckConstraint("amount_claimed > 0", name="claimed_positive"),
        CheckConstraint(
            "amount_refunded IS NULL"
            " OR (amount_refunded >= 0 AND amount_refunded <= amount_claimed)",
            name="refunded_within_claim",
        ),
        CheckConstraint("(status = 'paid') = (amount_refunded > 0)", name="paid_has_amount"),
        CheckConstraint("(status = 'open') = (settled_at IS NULL)", name="settled_has_time"),
        Index("ix_refunds_wallet_status", "wallet_id", "status"),
        Index("ix_refunds_status_sla", "status", "sla_due_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # One claim per payment.
    txn_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("transactions.txn_id", ondelete="CASCADE"), unique=True
    )
    victim_id: Mapped[str] = mapped_column(String(32), index=True)  # who sent it, and is paid back
    wallet_id: Mapped[str] = mapped_column(String(32))  # where it went: the reported wallet
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), index=True)
    report_id: Mapped[int | None] = mapped_column(ForeignKey("customer_reports.id"))
    amount_claimed: Mapped[float] = mapped_column(Money)
    amount_refunded: Mapped[float | None] = mapped_column(Money)
    status: Mapped[str] = mapped_column(String(16), server_default="open")
    # Why it ended as it did: confirmed_fraud, not_confirmed, nothing_left, not_a_victim.
    outcome: Mapped[str | None] = mapped_column(String(20))
    filed_at: Mapped[datetime] = mapped_column(Timestamp)  # domain time, like opened_at
    sla_due_at: Mapped[datetime] = mapped_column(Timestamp)
    settled_at: Mapped[datetime | None] = mapped_column(Timestamp)
    settled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())


class PastCase(Base):
    """Confirmed fraud cases from before the platform went live (the training period)."""

    __tablename__ = "past_cases"

    case_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    typology: Mapped[str] = mapped_column(String(32))
    victim_id: Mapped[str] = mapped_column(String(32))
    mule_id: Mapped[str] = mapped_column(String(32), index=True)
    started_at: Mapped[datetime] = mapped_column(Timestamp)
    loss: Mapped[float] = mapped_column(Money)
    n_txn: Mapped[int] = mapped_column(Integer)


class AuditLog(Base):
    """Append-only: a database trigger rejects UPDATE, DELETE and TRUNCATE."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_object", "object_type", "object_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now(), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    actor: Mapped[str] = mapped_column(String(64))  # username, or what was typed at a failed login
    role: Mapped[str | None] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(48), index=True)
    object_type: Mapped[str | None] = mapped_column(String(24))
    object_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    request_id: Mapped[str | None] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(String(64))
