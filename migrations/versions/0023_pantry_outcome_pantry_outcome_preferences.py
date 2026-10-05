"""pantry outcome preferences

Revision ID: 0023_pantry_outcome
Revises: 0022_receipt_store_batch
Create Date: 2026-10-04 23:09:18.436923
"""

import sqlalchemy as sa
from alembic import op

revision = "0023_pantry_outcome"
down_revision = "0022_receipt_store_batch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pantryoutcome",
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("household_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("normalized_name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("origin_on", sa.Date(), nullable=False),
        sa.Column("expires_on", sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["pantryitem.id"],
        ),
        sa.PrimaryKeyConstraint("item_id"),
    )
    with op.batch_alter_table("pantryoutcome", schema=None) as batch_op:
        batch_op.create_index(
            "ix_outcome_household_user", ["household_id", "user_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("pantryoutcome", schema=None) as batch_op:
        batch_op.drop_index("ix_outcome_household_user")

    op.drop_table("pantryoutcome")
