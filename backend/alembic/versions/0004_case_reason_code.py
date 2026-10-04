"""case reason code

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-04 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CODES = ("known_recipient", "family_transfer", "merchant", "new_phone", "other")


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("cases", sa.Column("reason_code", sa.String(length=24), nullable=True))
    op.create_check_constraint(
        op.f("ck_cases_reason_code"),
        "cases",
        "reason_code IS NULL OR reason_code IN (" + ", ".join(repr(c) for c in CODES) + ")",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("ck_cases_reason_code", "cases", type_="check")
    op.drop_column("cases", "reason_code")
