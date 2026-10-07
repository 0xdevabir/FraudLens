"""refunds for scam victims

Revision ID: 0006_refunds
Revises: 0005
Create Date: 2026-10-07 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_refunds"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "refunds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("txn_id", sa.BigInteger(), nullable=False),
        sa.Column("victim_id", sa.String(length=32), nullable=False),
        sa.Column("wallet_id", sa.String(length=32), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=True),
        sa.Column("report_id", sa.Integer(), nullable=True),
        sa.Column("amount_claimed", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("amount_refunded", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="open", nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=True),
        sa.Column("filed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sla_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settled_by", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('open', 'paid', 'unrecoverable', 'declined')",
            name=op.f("ck_refunds_status"),
        ),
        sa.CheckConstraint("amount_claimed > 0", name=op.f("ck_refunds_claimed_positive")),
        sa.CheckConstraint(
            "amount_refunded IS NULL"
            " OR (amount_refunded >= 0 AND amount_refunded <= amount_claimed)",
            name=op.f("ck_refunds_refunded_within_claim"),
        ),
        sa.CheckConstraint(
            "(status = 'paid') = (amount_refunded > 0)", name=op.f("ck_refunds_paid_has_amount")
        ),
        sa.CheckConstraint(
            "(status = 'open') = (settled_at IS NULL)", name=op.f("ck_refunds_settled_has_time")
        ),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], name=op.f("fk_refunds_case_id_cases")),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["customer_reports.id"],
            name=op.f("fk_refunds_report_id_customer_reports"),
        ),
        sa.ForeignKeyConstraint(
            ["settled_by"], ["users.id"], name=op.f("fk_refunds_settled_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["txn_id"],
            ["transactions.txn_id"],
            name=op.f("fk_refunds_txn_id_transactions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refunds")),
        sa.UniqueConstraint("txn_id", name=op.f("uq_refunds_txn_id")),
    )
    op.create_index(op.f("ix_refunds_case_id"), "refunds", ["case_id"], unique=False)
    op.create_index(op.f("ix_refunds_victim_id"), "refunds", ["victim_id"], unique=False)
    op.create_index("ix_refunds_wallet_status", "refunds", ["wallet_id", "status"], unique=False)
    op.create_index("ix_refunds_status_sla", "refunds", ["status", "sla_due_at"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_refunds_status_sla", table_name="refunds")
    op.drop_index("ix_refunds_wallet_status", table_name="refunds")
    op.drop_index(op.f("ix_refunds_victim_id"), table_name="refunds")
    op.drop_index(op.f("ix_refunds_case_id"), table_name="refunds")
    op.drop_table("refunds")
