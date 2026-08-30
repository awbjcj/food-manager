"""Bind Telegram group chats to households."""

import sqlalchemy as sa
from alembic import op

revision: str = "0021_group_binding"
down_revision: str | None = "0020_provider_mode_override"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "groupbinding",
        sa.Column("chat_id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(), nullable=False),
        sa.Column("bound_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["household_id"], ["household.id"]),
    )
    op.create_index(
        "ix_groupbinding_household_id",
        "groupbinding",
        ["household_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_groupbinding_household_id", table_name="groupbinding")
    op.drop_table("groupbinding")
