"""Cooked meals (v5.5)."""

import sqlalchemy as sa
from alembic import op

revision: str = "0018_cooked_meal"
down_revision: str | None = "0017_billing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cookedmeal",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(), sa.ForeignKey("household.id"), nullable=False, index=True),
        sa.Column("source", sa.String(), nullable=False, server_default="plan"),
        sa.Column("plan_entry_id", sa.Integer(), sa.ForeignKey("mealplanentry.id"), nullable=True),
        sa.Column("recipe_key", sa.String(), nullable=False, index=True),
        sa.Column("recipe_title", sa.String(), nullable=False),
        sa.Column("cooked_on", sa.Date(), nullable=False),
        sa.Column("selection_json", sa.String(), nullable=False, server_default="[]"),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_cooked_household_confirmed", "cookedmeal", ["household_id", "confirmed_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_cooked_household_confirmed", table_name="cookedmeal")
    op.drop_table("cookedmeal")
