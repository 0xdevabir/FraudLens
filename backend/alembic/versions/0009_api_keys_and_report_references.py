"""api keys and customer report references

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-04 15:00:00.000000

"""

import secrets
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# No 0, O, 1, I or L: a reference is read aloud and typed from a phone.
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _reference() -> str:
    code = "".join(secrets.choice(ALPHABET) for _ in range(8))
    return f"FL-{code[:4]}-{code[4:]}"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("prefix", sa.String(length=12), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.String(length=16)), nullable=False),
        sa.Column("sandbox", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("rate_per_minute", sa.Integer(), server_default=sa.text("600"), nullable=False),
        sa.Column("daily_quota", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("rate_per_minute > 0", name=op.f("ck_api_keys_rate_positive")),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_api_keys_created_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_keys")),
        sa.UniqueConstraint("prefix", name=op.f("uq_api_keys_prefix")),
    )
    op.add_column("customer_reports", sa.Column("reference", sa.String(length=16), nullable=True))
    # Reports made before references existed get one too.
    connection = op.get_bind()
    for (report_id,) in connection.execute(sa.text("SELECT id FROM customer_reports")).all():
        connection.execute(
            sa.text("UPDATE customer_reports SET reference = :ref WHERE id = :id"),
            {"ref": _reference(), "id": report_id},
        )
    op.create_unique_constraint(
        op.f("uq_customer_reports_reference"), "customer_reports", ["reference"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f("uq_customer_reports_reference"), "customer_reports", type_="unique")
    op.drop_column("customer_reports", "reference")
    op.drop_table("api_keys")
