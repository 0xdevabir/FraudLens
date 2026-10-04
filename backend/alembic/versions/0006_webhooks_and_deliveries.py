"""webhook endpoints and the delivery outbox

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-04 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "webhook_endpoints",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("description", sa.String(length=200), server_default="", nullable=False),
        sa.Column("secret", sa.String(length=128), nullable=False),
        sa.Column("events", postgresql.ARRAY(sa.String(length=40)), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_webhook_endpoints_created_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_endpoints")),
    )
    op.create_table(
        "deliveries",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("endpoint_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("event_id", sa.String(length=40), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=10), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_status_code", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.String(length=300), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint("kind IN ('webhook', 'sms')", name=op.f("ck_deliveries_kind")),
        sa.CheckConstraint(
            "status IN ('pending', 'delivered', 'dead')", name=op.f("ck_deliveries_status")
        ),
        sa.ForeignKeyConstraint(
            ["endpoint_id"],
            ["webhook_endpoints.id"],
            name=op.f("fk_deliveries_endpoint_id_webhook_endpoints"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deliveries")),
    )
    op.create_index(
        "ix_deliveries_due",
        "deliveries",
        ["next_attempt_at"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_deliveries_endpoint", "deliveries", ["endpoint_id", sa.text("id DESC")], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_deliveries_endpoint", table_name="deliveries")
    op.drop_index("ix_deliveries_due", table_name="deliveries")
    op.drop_table("deliveries")
    op.drop_table("webhook_endpoints")
