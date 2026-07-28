"""user_food_profile

Revision ID: 0004_user_food_profile
Revises: 0003_user_llm_provider
Create Date: 2026-05-30
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_user_food_profile"
down_revision: str | None = "0003_user_llm_provider"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("user", sa.Column("diet", sa.String(), nullable=False, server_default="none"))
    op.add_column("user", sa.Column("exclusions_json", sa.String(), nullable=False, server_default="[]"))
    op.add_column(
        "user",
        sa.Column("preferred_cuisines_json", sa.String(), nullable=False, server_default="[]"),
    )
    op.add_column("user", sa.Column("max_cook_minutes", sa.Integer(), nullable=True))
    op.add_column("user", sa.Column("household_size", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("user", sa.Column("profile_note", sa.String(), nullable=False, server_default=""))


def downgrade() -> None:
    for col in (
        "profile_note",
        "household_size",
        "max_cook_minutes",
        "preferred_cuisines_json",
        "exclusions_json",
        "diet",
    ):
        op.drop_column("user", col)
