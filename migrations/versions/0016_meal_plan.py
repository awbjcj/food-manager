"""Meal plan tables (v5.2)."""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016_meal_plan"
down_revision: Union[str, None] = "0015_user_last_digest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mealplan",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(), sa.ForeignKey("household.id"), nullable=False, index=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("cost_micros_usd", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "mealplanentry",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("mealplan.id"), nullable=False, index=True),
        sa.Column("day_index", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("recipe_json", sa.String(), nullable=False),
        sa.Column("spec_json", sa.String(), nullable=False),
        sa.Column("shopping_json", sa.String(), nullable=False, server_default="[]"),
        sa.Column("search_offset", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("mealplanentry")
    op.drop_table("mealplan")
