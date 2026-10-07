"""pending decisions

Revision ID: 0005
Revises: 0004_appeals
Create Date: 2026-10-07 09:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004_appeals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "pending_decisions",
        sa.Column("txn_id", sa.BigInteger(), nullable=False),
        sa.Column("features", postgresql.ARRAY(sa.Float(precision=53)), nullable=False),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["txn_id"],
            ["transactions.txn_id"],
            name=op.f("fk_pending_decisions_txn_id_transactions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("txn_id", name=op.f("pk_pending_decisions")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("pending_decisions")
