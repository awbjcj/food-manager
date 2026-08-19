"""Track subscription cancellation without revoking the paid period."""

import sqlalchemy as sa
from alembic import op

revision = "0019_subscription_cancel_at_period_end"
down_revision = "0018_cooked_meal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "subscription",
        sa.Column(
            "cancel_at_period_end",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("subscription", "cancel_at_period_end")
