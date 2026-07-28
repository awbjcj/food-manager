"""user_lang

Revision ID: 0008_user_lang
Revises: 0007_household
Create Date: 2026-05-31
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_user_lang"
down_revision: str | None = "0007_household"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column("lang", sa.String(), nullable=False, server_default="en"),
    )


def downgrade() -> None:
    op.drop_column("user", "lang")
