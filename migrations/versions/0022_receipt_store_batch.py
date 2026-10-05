"""Receipt store identity and persisted batch selections."""

import sqlalchemy as sa
from alembic import op

revision = "0022_receipt_store_batch"
down_revision = "0021_group_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("receipt", sa.Column("store_name", sa.String(), nullable=True))
    op.create_table(
        "pantrybatch",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("household_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("candidate_ids_json", sa.String(), nullable=False),
        sa.Column("selected_ids_json", sa.String(), nullable=False),
        sa.Column("sort_by", sa.String(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("target_status", sa.String(), nullable=True),
        sa.Column("applied_count", sa.Integer(), nullable=False),
        sa.Column("skipped_count", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["household_id"], ["household.id"]),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_pantrybatch_household_id", "pantrybatch", ["household_id"])
    op.create_index("ix_pantrybatch_user_id", "pantrybatch", ["user_id"])


def downgrade() -> None:
    op.drop_table("pantrybatch")
    with op.batch_alter_table("receipt") as batch_op:
        batch_op.drop_column("store_name")
