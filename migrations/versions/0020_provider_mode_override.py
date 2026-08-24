"""Operator-set LLM credential mode overrides (api vs sub2api subscription)."""

import sqlalchemy as sa
from alembic import op

revision: str = "0020_provider_mode_override"
down_revision: str | None = "0019_subscription_cancel_at_period_end"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "providermodeoverride",
        sa.Column("provider", sa.String(), primary_key=True),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("providermodeoverride")
