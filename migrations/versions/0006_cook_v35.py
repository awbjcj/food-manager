"""cook v3.5: feedback columns, shopping list, saved recipes

Revision ID: 0006_cook_v35
Revises: 0005_cook_session
Create Date: 2026-05-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_cook_v35"
down_revision: Union[str, None] = "0005_cook_session"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "cooksession",
        sa.Column("feedback", sa.String(), nullable=False, server_default="none"),
    )
    op.add_column(
        "cooksession",
        sa.Column("feedback_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "shoppinglist",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.telegram_id"), nullable=False),
        sa.Column("name_raw", sa.String(), nullable=False),
        sa.Column("name_normalized", sa.String(), nullable=False),
        sa.Column("qty", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(), nullable=True),
        sa.Column("added_at", sa.DateTime(), nullable=False),
        sa.Column("bought_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_shoppinglist_user_id", "shoppinglist", ["user_id"])
    op.create_index("ix_shoppinglist_name_normalized", "shoppinglist", ["name_normalized"])

    op.create_table(
        "savedrecipe",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.telegram_id"), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("cuisine", sa.String(), nullable=False),
        sa.Column("source_url", sa.String(), nullable=True),
        sa.Column("ingredients_json", sa.String(), nullable=False),
        sa.Column("method_gist", sa.String(), nullable=False),
        sa.Column("saved_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_savedrecipe_user_id", "savedrecipe", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_savedrecipe_user_id", table_name="savedrecipe")
    op.drop_table("savedrecipe")
    op.drop_index("ix_shoppinglist_name_normalized", table_name="shoppinglist")
    op.drop_index("ix_shoppinglist_user_id", table_name="shoppinglist")
    op.drop_table("shoppinglist")
    op.drop_column("cooksession", "feedback_at")
    op.drop_column("cooksession", "feedback")
