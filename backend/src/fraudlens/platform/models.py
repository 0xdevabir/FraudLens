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
