"""initial

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-26
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user",
        sa.Column("telegram_id", sa.Integer(), nullable=False),
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("tz", sa.String(), nullable=False),
        sa.Column("digest_hour", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("telegram_id"),
    )
    op.create_table(
        "receipt",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("photo_file_id", sa.String(), nullable=False),
        sa.Column("purchase_date", sa.Date(), nullable=False),
        sa.Column("purchase_date_source", sa.String(), nullable=False),
        sa.Column("scanned_at", sa.DateTime(), nullable=False),
        sa.Column("llm_cost_micros_usd", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.telegram_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "photo_file_id", name="uq_receipt_user_photo"),
    )
    op.create_index(op.f("ix_receipt_user_id"), "receipt", ["user_id"], unique=False)
    op.create_table(
        "shelflifecache",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("normalized_name", sa.String(), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("learned_at", sa.DateTime(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.telegram_id"]),
        sa.PrimaryKeyConstraint("user_id", "normalized_name"),
    )
    op.create_table(
        "pantryitem",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("raw_name", sa.String(), nullable=False),
        sa.Column("normalized_name", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(), nullable=True),
        sa.Column("purchased_on", sa.Date(), nullable=False),
        sa.Column("shelf_life_days", sa.Integer(), nullable=False),
        sa.Column("shelf_life_source", sa.String(), nullable=False),
        sa.Column("ingest_shelf_life_source", sa.String(), nullable=False),
        sa.Column("expires_on", sa.Date(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("snoozed_until", sa.Date(), nullable=True),
        sa.Column("created_via", sa.String(), nullable=False),
        sa.Column("source_receipt_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["source_receipt_id"], ["receipt.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.telegram_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_pantryitem_category"), "pantryitem", ["category"], unique=False)
    op.create_index(op.f("ix_pantryitem_normalized_name"), "pantryitem", ["normalized_name"], unique=False)
    op.create_index(op.f("ix_pantryitem_user_id"), "pantryitem", ["user_id"], unique=False)
    op.create_index("ix_pantry_source_receipt", "pantryitem", ["source_receipt_id"], unique=False)
    op.create_index(
        "ix_pantry_user_status_category_expires",
        "pantryitem",
        ["user_id", "status", "category", "expires_on"],
        unique=False,
    )
    op.create_index(
        "ix_pantry_user_status_expires",
        "pantryitem",
        ["user_id", "status", "expires_on"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_pantry_user_status_expires", table_name="pantryitem")
    op.drop_index("ix_pantry_user_status_category_expires", table_name="pantryitem")
    op.drop_index("ix_pantry_source_receipt", table_name="pantryitem")
    op.drop_index(op.f("ix_pantryitem_user_id"), table_name="pantryitem")
    op.drop_index(op.f("ix_pantryitem_normalized_name"), table_name="pantryitem")
    op.drop_index(op.f("ix_pantryitem_category"), table_name="pantryitem")
    op.drop_table("pantryitem")
    op.drop_table("shelflifecache")
    op.drop_index(op.f("ix_receipt_user_id"), table_name="receipt")
    op.drop_table("receipt")
    op.drop_table("user")
