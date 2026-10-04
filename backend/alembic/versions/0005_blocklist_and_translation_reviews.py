"""blocklist and translation reviews

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-04 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "blocklist",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("value", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=12), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("removed_by", sa.Integer(), nullable=True),
        sa.Column("remove_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint("kind IN ('wallet', 'phone', 'url')", name=op.f("ck_blocklist_kind")),
        sa.CheckConstraint(
            "source IN ('manual', 'verdict', 'import')", name=op.f("ck_blocklist_source")
        ),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], name=op.f("fk_blocklist_case_id_cases")),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_blocklist_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["removed_by"], ["users.id"], name=op.f("fk_blocklist_removed_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_blocklist")),
    )
    op.create_index(
        "uq_blocklist_active",
        "blocklist",
        ["kind", "value"],
        unique=True,
        postgresql_where=sa.text("removed_at IS NULL"),
    )
    op.create_table(
        "translation_reviews",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), nullable=False),
        sa.Column("reviewer_name", sa.String(length=120), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "reviewed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('approved', 'changes_requested')", name=op.f("ck_translation_reviews_status")
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_id"], ["users.id"], name=op.f("fk_translation_reviews_reviewer_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_translation_reviews")),
    )
    op.create_index(
        "ix_translation_reviews_key",
        "translation_reviews",
        ["key", sa.text("id DESC")],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_translation_reviews_key", table_name="translation_reviews")
    op.drop_table("translation_reviews")
    op.drop_index("uq_blocklist_active", table_name="blocklist")
    op.drop_table("blocklist")
