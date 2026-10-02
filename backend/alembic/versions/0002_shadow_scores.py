"""shadow scores

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-01 21:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "shadow_scores",
        sa.Column("txn_id", sa.BigInteger(), nullable=False),
        sa.Column("model_version", sa.String(length=16), nullable=False),
        sa.Column("risk", sa.Float(precision=53), nullable=False),
        sa.Column("tier", sa.String(length=8), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "tier IN ('allow', 'warn', 'step_up', 'hold')", name=op.f("ck_shadow_scores_tier")
        ),
        sa.ForeignKeyConstraint(
            ["txn_id"],
            ["decisions.txn_id"],
            name=op.f("fk_shadow_scores_txn_id_decisions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("txn_id", "model_version", name=op.f("pk_shadow_scores")),
    )
    op.create_index("ix_shadow_scores_model_version", "shadow_scores", ["model_version"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_shadow_scores_model_version", table_name="shadow_scores")
    op.drop_table("shadow_scores")
