"""cook_session

Revision ID: 0005_cook_session
Revises: 0004_user_food_profile
Create Date: 2026-05-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_cook_session"
down_revision: Union[str, None] = "0004_user_food_profile"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cooksession",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.telegram_id"), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("meal_type", sa.String(), nullable=True),
        sa.Column("cuisine", sa.String(), nullable=True),
        sa.Column("selected_item_ids", sa.String(), nullable=False),
        sa.Column("candidates_json", sa.String(), nullable=True),
        sa.Column("chosen_index", sa.Integer(), nullable=True),
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("llm_cost_micros_usd", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_cook_user_status_created", "cooksession",
                    ["user_id", "status", "created_at"])
    op.create_index("ix_cooksession_user_id", "cooksession", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_cooksession_user_id", table_name="cooksession")
    op.drop_index("ix_cook_user_status_created", table_name="cooksession")
    op.drop_table("cooksession")
