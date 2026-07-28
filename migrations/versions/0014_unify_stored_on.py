"""unify stored_on

Replace the frozen-only `frozen_on` column with a single `stored_on` Storage
Date that serves as the Shelf-Life Origin for any non-default Storage State
(fridge or frozen). Existing frozen rows carry their `frozen_on` forward.

Revision ID: 0014_unify_stored_on
Revises: 0013_cook_purpose_offset
Create Date: 2026-06-13
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_unify_stored_on"
down_revision: str | None = "0013_cook_purpose_offset"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pantryitem",
        sa.Column("stored_on", sa.Date(), nullable=True),
    )
    # Carry forward existing frozen origins into the unified column.
    op.execute("UPDATE pantryitem SET stored_on = frozen_on WHERE frozen_on IS NOT NULL")
    with op.batch_alter_table("pantryitem") as batch_op:
        batch_op.drop_column("frozen_on")


def downgrade() -> None:
    op.add_column(
        "pantryitem",
        sa.Column("frozen_on", sa.Date(), nullable=True),
    )
    # Only frozen rows had a frozen_on; restore those.
    op.execute(
        "UPDATE pantryitem SET frozen_on = stored_on "
        "WHERE storage = 'frozen' AND stored_on IS NOT NULL"
    )
    with op.batch_alter_table("pantryitem") as batch_op:
        batch_op.drop_column("stored_on")
