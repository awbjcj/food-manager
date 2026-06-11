"""cook session purpose + search offset

Revision ID: 0013_cook_purpose_offset
Revises: 0012_frozen_storage
Create Date: 2026-06-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0013_cook_purpose_offset"
down_revision: Union[str, None] = "0012_frozen_storage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("cooksession", sa.Column("purpose", sa.String(), nullable=True))
    op.add_column(
        "cooksession",
        sa.Column("search_offset", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("cooksession", "search_offset")
    op.drop_column("cooksession", "purpose")
