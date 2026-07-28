"""frozen_storage

Revision ID: 0012_frozen_storage
Revises: 0011_invite_multi_use
Create Date: 2026-06-08
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_frozen_storage"
down_revision: str | None = "0011_invite_multi_use"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pantryitem",
        sa.Column("storage", sa.String(), nullable=False, server_default="default"),
    )
    op.add_column(
        "pantryitem",
        sa.Column("frozen_on", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pantryitem", "frozen_on")
    op.drop_column("pantryitem", "storage")
