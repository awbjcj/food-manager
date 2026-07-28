# migrations/versions/0007_household.py
"""v4.0: Household tenancy — create household, backfill, re-key shared tables.

Revision ID: 0007_household
Revises: 0006_cook_v35
Create Date: 2026-05-31
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_household"
down_revision: str | None = "0006_cook_v35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SIMPLE = ["receipt", "pantryitem", "pendingcorrection", "cooksession",
           "shoppinglist", "savedrecipe"]


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "household",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False, server_default="My Household"),
        sa.Column("diet", sa.String(), nullable=False, server_default="none"),
        sa.Column("exclusions_json", sa.String(), nullable=False, server_default="[]"),
        sa.Column("preferred_cuisines_json", sa.String(), nullable=False, server_default="[]"),
        sa.Column("max_cook_minutes", sa.Integer(), nullable=True),
        sa.Column("household_size", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("profile_note", sa.String(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.add_column("user", sa.Column("household_id", sa.Integer(), nullable=True))
    users = bind.execute(sa.text(
        "SELECT telegram_id, diet, exclusions_json, preferred_cuisines_json, "
        "max_cook_minutes, household_size, profile_note, created_at FROM user"
    )).fetchall()
    for u in users:
        res = bind.execute(sa.text(
            "INSERT INTO household (name, diet, exclusions_json, preferred_cuisines_json, "
            "max_cook_minutes, household_size, profile_note, created_at) VALUES "
            "('My Household', :diet, :excl, :cuis, :maxc, :size, :note, :created)"
        ), {"diet": u.diet, "excl": u.exclusions_json, "cuis": u.preferred_cuisines_json,
            "maxc": u.max_cook_minutes, "size": u.household_size,
            "note": u.profile_note, "created": u.created_at})
        hid = res.lastrowid
        bind.execute(sa.text("UPDATE user SET household_id = :hid WHERE telegram_id = :tid"),
                     {"hid": hid, "tid": u.telegram_id})

    for tbl in _SIMPLE:
        op.add_column(tbl, sa.Column("household_id", sa.Integer(), nullable=True))
        bind.execute(sa.text(
            f"UPDATE {tbl} SET household_id = "
            f"(SELECT household_id FROM user WHERE user.telegram_id = {tbl}.user_id)"
        ))
    op.add_column("shelflifecache", sa.Column("household_id", sa.Integer(), nullable=True))
    bind.execute(sa.text(
        "UPDATE shelflifecache SET household_id = "
        "(SELECT household_id FROM user WHERE user.telegram_id = shelflifecache.user_id)"
    ))

    # Every shared row must map to a household. A leftover NULL means an orphaned
    # row (user_id with no surviving user); fail loudly now rather than with a
    # cryptic NOT NULL error mid-rebuild. (shelflifecache orphans are intentionally
    # dropped below — it's a regenerable cache, not user data.)
    for tbl in _SIMPLE:
        orphans = bind.execute(sa.text(
            f"SELECT COUNT(*) FROM {tbl} WHERE household_id IS NULL"
        )).scalar()
        if orphans:
            raise RuntimeError(
                f"0007 aborted: {orphans} row(s) in '{tbl}' have a user_id with no "
                f"matching user and cannot be assigned a household_id. "
                f"Remove or re-own them, then re-run."
            )

    with op.batch_alter_table("user") as b:
        b.alter_column("household_id", existing_type=sa.Integer(), nullable=False)
        b.create_foreign_key("fk_user_household", "household", ["household_id"], ["id"])
        b.create_index("ix_user_household_id", ["household_id"])
        for col in ("diet", "exclusions_json", "preferred_cuisines_json",
                    "max_cook_minutes", "household_size", "profile_note"):
            b.drop_column(col)

    op.drop_index("ix_receipt_user_id", table_name="receipt")
    with op.batch_alter_table("receipt") as b:
        b.alter_column("household_id", existing_type=sa.Integer(), nullable=False)
        b.create_foreign_key("fk_receipt_household", "household", ["household_id"], ["id"])
        b.create_index("ix_receipt_household_id", ["household_id"])
        b.create_unique_constraint("uq_receipt_household_photo", ["household_id", "photo_file_id"])
        b.drop_column("user_id")

    op.drop_index("ix_pantryitem_user_id", table_name="pantryitem")
    op.drop_index("ix_pantry_user_status_expires", table_name="pantryitem")
    op.drop_index("ix_pantry_user_status_category_expires", table_name="pantryitem")
    with op.batch_alter_table("pantryitem") as b:
        b.alter_column("household_id", existing_type=sa.Integer(), nullable=False)
        b.create_foreign_key("fk_pantry_household", "household", ["household_id"], ["id"])
        b.create_index("ix_pantryitem_household_id", ["household_id"])
        b.create_index("ix_pantry_household_status_expires",
                       ["household_id", "status", "expires_on"])
        b.create_index("ix_pantry_household_status_category_expires",
                       ["household_id", "status", "category", "expires_on"])
        b.drop_column("user_id")

    op.drop_index("ix_pendingcorrection_user_id", table_name="pendingcorrection")
    op.drop_index("ix_pending_user_status_created", table_name="pendingcorrection")
    with op.batch_alter_table("pendingcorrection") as b:
        b.alter_column("household_id", existing_type=sa.Integer(), nullable=False)
        b.create_foreign_key("fk_pending_household", "household", ["household_id"], ["id"])
        b.create_index("ix_pendingcorrection_household_id", ["household_id"])
        b.create_index("ix_pending_household_status_created",
                       ["household_id", "status", "created_at"])
        b.drop_column("user_id")

    op.drop_index("ix_cooksession_user_id", table_name="cooksession")
    op.drop_index("ix_cook_user_status_created", table_name="cooksession")
    with op.batch_alter_table("cooksession") as b:
        b.alter_column("household_id", existing_type=sa.Integer(), nullable=False)
        b.create_foreign_key("fk_cook_household", "household", ["household_id"], ["id"])
        b.create_index("ix_cooksession_household_id", ["household_id"])
        b.create_index("ix_cook_household_status_created",
                       ["household_id", "status", "created_at"])
        b.drop_column("user_id")

    op.drop_index("ix_shoppinglist_user_id", table_name="shoppinglist")
    with op.batch_alter_table("shoppinglist") as b:
        b.alter_column("household_id", existing_type=sa.Integer(), nullable=False)
        b.create_foreign_key("fk_shopping_household", "household", ["household_id"], ["id"])
        b.create_index("ix_shoppinglist_household_id", ["household_id"])
        b.drop_column("user_id")
    op.drop_index("ix_savedrecipe_user_id", table_name="savedrecipe")
    with op.batch_alter_table("savedrecipe") as b:
        b.alter_column("household_id", existing_type=sa.Integer(), nullable=False)
        b.create_foreign_key("fk_saved_household", "household", ["household_id"], ["id"])
        b.create_index("ix_savedrecipe_household_id", ["household_id"])
        b.drop_column("user_id")

    op.create_table(
        "shelflifecache_new",
        sa.Column("household_id", sa.Integer(), sa.ForeignKey("household.id"), primary_key=True),
        sa.Column("normalized_name", sa.String(), primary_key=True),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("learned_at", sa.DateTime(), nullable=False),
        sa.Column("source", sa.String(), nullable=False, server_default="llm"),
    )
    bind.execute(sa.text(
        "INSERT INTO shelflifecache_new "
        "(household_id, normalized_name, days, category, confidence, learned_at, source) "
        "SELECT household_id, normalized_name, days, category, confidence, learned_at, source "
        "FROM shelflifecache WHERE household_id IS NOT NULL"
    ))
    op.drop_table("shelflifecache")
    op.rename_table("shelflifecache_new", "shelflifecache")


def downgrade() -> None:
    raise NotImplementedError("0007 household migration is forward-only")
