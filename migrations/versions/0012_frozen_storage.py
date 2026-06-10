"""frozen_storage

Revision ID: 0012_frozen_storage
Revises: 0011_invite_multi_use
Create Date: 2026-06-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0012_frozen_storage"
down_revision: Union[str, None] = "0011_invite_multi_use"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
