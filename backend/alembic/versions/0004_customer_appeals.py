"""customer appeals

Revision ID: 0004_appeals
Revises: 0003
Create Date: 2026-10-07 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_appeals"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "appeals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("txn_id", sa.BigInteger(), nullable=False),
        sa.Column("wallet_id", sa.String(length=32), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=True),
        sa.Column("tier", sa.String(length=8), nullable=False),
        sa.Column("relation", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=12), server_default="pending", nullable=False),
        sa.Column("filed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sla_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by", sa.Integer(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'pending') = (decided_by IS NULL)",
            name=op.f("ck_appeals_decided_has_decider"),
        ),
        sa.CheckConstraint(
            "relation IN ('family', 'friend', 'business', 'seller', 'landlord', 'employer', "
            "'other', 'none')",
            name=op.f("ck_appeals_relation"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')", name=op.f("ck_appeals_status")
        ),
        sa.CheckConstraint("tier IN ('warn', 'step_up', 'hold')", name=op.f("ck_appeals_tier")),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], name=op.f("fk_appeals_case_id_cases")),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["users.id"], name=op.f("fk_appeals_decided_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["txn_id"],
            ["transactions.txn_id"],
            name=op.f("fk_appeals_txn_id_transactions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_appeals")),
        sa.UniqueConstraint("txn_id", name=op.f("uq_appeals_txn_id")),
    )
    op.create_index("ix_appeals_status_sla", "appeals", ["status", "sla_due_at"], unique=False)
    op.create_index(op.f("ix_appeals_wallet_id"), "appeals", ["wallet_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_appeals_wallet_id"), table_name="appeals")
    op.drop_index("ix_appeals_status_sla", table_name="appeals")
    op.drop_table("appeals")
