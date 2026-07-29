"""Billing subscriptions, quota usage, ledger, and bans."""

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from alembic import op

revision = "0017_billing"
down_revision = "0016_meal_plan"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "subscription",
        sa.Column(
            "household_id",
            sa.Integer(),
            sa.ForeignKey("household.id"),
            primary_key=True,
        ),
        sa.Column("tier", sa.String(), nullable=False, server_default="free"),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("telegram_charge_id", sa.String(), nullable=True),
        sa.Column("payer_telegram_id", sa.Integer(), nullable=True),
        sa.Column("period_start", sa.DateTime(), nullable=False),
        sa.Column("period_end", sa.DateTime(), nullable=False),
        sa.Column("seat_cap", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "quotausage",
        sa.Column(
            "household_id",
            sa.Integer(),
            sa.ForeignKey("household.id"),
            primary_key=True,
        ),
        sa.Column("period_start", sa.DateTime(), primary_key=True),
        *(
            sa.Column(name, sa.Integer(), nullable=False, server_default="0")
            for name in (
                "receipts_used",
                "actions_used",
                "cook_used",
                "plan_used",
                "edit_used",
                "chat_used",
                "search_used",
                "cost_micros_used",
                "receipts_granted",
                "actions_granted",
                "cost_micros_granted",
            )
        ),
    )
    op.create_table(
        "paymentevent",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "household_id", sa.Integer(), sa.ForeignKey("household.id"), nullable=False
        ),
        sa.Column("telegram_charge_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("sku", sa.String(), nullable=False),
        sa.Column("stars", sa.Integer(), nullable=False),
        sa.Column("payer_telegram_id", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_paymentevent_household_id", "paymentevent", ["household_id"])
    op.create_index(
        "ix_paymentevent_telegram_charge_id",
        "paymentevent",
        ["telegram_charge_id"],
        unique=True,
    )
    op.create_index(
        "ix_payment_household_created", "paymentevent", ["household_id", "created_at"]
    )
    op.add_column(
        "user",
        sa.Column("banned", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    now = datetime.now(UTC).replace(tzinfo=None)
    op.execute(
        sa.text(
            "INSERT INTO subscription (household_id, tier, status, period_start, "
            "period_end, seat_cap, created_at, updated_at) "
            "SELECT id, 'family', 'active', :start, :end, 10, :start, :start FROM household"
        ).bindparams(start=now, end=now + timedelta(days=30))
    )


def downgrade() -> None:
    op.drop_column("user", "banned")
    op.drop_index("ix_payment_household_created", table_name="paymentevent")
    op.drop_index("ix_paymentevent_telegram_charge_id", table_name="paymentevent")
    op.drop_index("ix_paymentevent_household_id", table_name="paymentevent")
    op.drop_table("paymentevent")
    op.drop_table("quotausage")
    op.drop_table("subscription")
