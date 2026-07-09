"""Add User.last_digest_date for the digest watchdog/catch-up.

Revision ID: 0015_user_last_digest
Revises: 0014_unify_stored_on
Create Date: 2026-07-08
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015_user_last_digest"
down_revision: Union[str, None] = "0014_unify_stored_on"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user", sa.Column("last_digest_date", sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_column("last_digest_date")
