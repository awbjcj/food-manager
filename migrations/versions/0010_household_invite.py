"""household_invite

Revision ID: 0010_household_invite
Revises: 0009_name_translation
Create Date: 2026-06-01
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_household_invite"
down_revision: str | None = "0009_name_translation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing users each own their (solo) household, so backfill them to
    # "owner". New members created via invite redemption default to "member".
    op.add_column(
        "user",
        sa.Column("role", sa.String(), nullable=False, server_default="owner"),
    )

    op.create_table(
        "householdinvite",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "household_id",
            sa.Integer(),
            sa.ForeignKey("household.id"),
            nullable=False,
        ),
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("redeemed_by", sa.Integer(), nullable=True),
        sa.Column("redeemed_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_householdinvite_token", "householdinvite", ["token"], unique=True
    )
    op.create_index(
        "ix_householdinvite_household_id", "householdinvite", ["household_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_householdinvite_household_id", table_name="householdinvite")
    op.drop_index("ix_householdinvite_token", table_name="householdinvite")
    op.drop_table("householdinvite")
    op.drop_column("user", "role")
