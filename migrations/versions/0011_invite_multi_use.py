"""invite_multi_use

Revision ID: 0011_invite_multi_use
Revises: 0010_household_invite
Create Date: 2026-06-01
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_invite_multi_use"
down_revision: str | None = "0010_household_invite"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # max_uses: NULL = unlimited until expiry. Deliberately NO server_default —
    # one would coerce app-inserted NULLs (family invites) back to 1. Existing
    # rows are backfilled to single-use (1) via UPDATE instead.
    op.add_column(
        "householdinvite",
        sa.Column("max_uses", sa.Integer(), nullable=True),
    )
    op.add_column(
        "householdinvite",
        sa.Column("uses", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute("UPDATE householdinvite SET max_uses = 1")
    # Pre-0011 single-use invites already redeemed have spent their one use.
    op.execute("UPDATE householdinvite SET uses = 1 WHERE redeemed_by IS NOT NULL")


def downgrade() -> None:
    op.drop_column("householdinvite", "uses")
    op.drop_column("householdinvite", "max_uses")
