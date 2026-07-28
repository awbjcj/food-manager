"""pending_correction

Revision ID: 0002_pending_correction
Revises: 0001_initial
Create Date: 2026-05-27
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_pending_correction"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pendingcorrection",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("action_type", sa.String(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=True),
        sa.Column("proposed_json", sa.String(), nullable=False),
        sa.Column("original_snapshot_json", sa.String(), nullable=True),
        sa.Column("llm_cost_micros_usd", sa.Integer(), nullable=True),
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["pantryitem.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.telegram_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pending_item", "pendingcorrection", ["item_id"], unique=False)
    op.create_index(
        "ix_pending_user_status_created",
        "pendingcorrection",
        ["user_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_pendingcorrection_user_id"),
        "pendingcorrection",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_pendingcorrection_user_id"), table_name="pendingcorrection")
    op.drop_index("ix_pending_user_status_created", table_name="pendingcorrection")
    op.drop_index("ix_pending_item", table_name="pendingcorrection")
    op.drop_table("pendingcorrection")
