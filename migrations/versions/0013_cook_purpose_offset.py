"""cook session purpose + search offset

Revision ID: 0013_cook_purpose_offset
Revises: 0012_frozen_storage
Create Date: 2026-06-10
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_cook_purpose_offset"
down_revision: str | None = "0012_frozen_storage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("cooksession", sa.Column("purpose", sa.String(), nullable=True))
    op.add_column(
        "cooksession",
        sa.Column("search_offset", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("cooksession", "search_offset")
    op.drop_column("cooksession", "purpose")
