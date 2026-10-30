"""messaging connections and internal identities

Revision ID: 385cb579c777
Revises: 0023_pantry_outcome
Create Date: 2026-10-30 00:06:51.892519
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "385cb579c777"
down_revision: str | None = "0023_pantry_outcome"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


NAMES = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


def upgrade() -> None:
    op.add_column("user", sa.Column("id", sa.Integer(), nullable=True))
    op.add_column(
        "user",
        sa.Column(
            "membership_active", sa.Boolean(), nullable=False, server_default="1"
        ),
    )
    op.add_column(
        "user",
        sa.Column(
            "membership_generation", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.execute("UPDATE user SET id = telegram_id")
    with op.batch_alter_table(
        "user", naming_convention=NAMES, recreate="always"
    ) as batch:
        batch.drop_constraint("pk_user", type_="primary")
        batch.create_primary_key("pk_user", ["id"])
        batch.alter_column("id", existing_type=sa.Integer(), nullable=False)
        batch.alter_column("telegram_id", existing_type=sa.Integer(), nullable=True)
        batch.alter_column("chat_id", existing_type=sa.Integer(), nullable=True)
        batch.alter_column("membership_active", server_default=None)
        batch.alter_column("membership_generation", server_default=None)
        batch.create_unique_constraint("uq_user_telegram_id", ["telegram_id"])
    with op.batch_alter_table("groupbinding", naming_convention=NAMES) as batch:
        batch.drop_constraint(
            "fk_groupbinding_bound_by_user_id_user", type_="foreignkey"
        )
        batch.create_foreign_key(
            "fk_groupbinding_bound_by_user_id_user",
            "user",
            ["bound_by_user_id"],
            ["id"],
        )
    op.create_table(
        "connectionadminsession",
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("csrf_hash", sa.String(), nullable=False),
        sa.Column("authority_hash", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_table(
        "messagingconnection",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("settings_json", sa.String(), nullable=False),
        sa.Column("secret_ciphertext", sa.String(), nullable=True),
        sa.Column("credential_source", sa.String(), nullable=False),
        sa.Column("external_account", sa.String(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("inbound_verified", sa.Boolean(), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("last_error_code", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "external_account"),
    )
    op.create_table(
        "messagingoperation",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("result_json", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "connectionoauthstate",
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("session_hash", sa.String(), nullable=False),
        sa.Column("connection_id", sa.String(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["messagingconnection.id"],
        ),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_table(
        "messaginginbox",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("connection_id", sa.String(), nullable=False),
        sa.Column("event_key", sa.String(), nullable=False),
        sa.Column("event_json", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["messagingconnection.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("connection_id", "event_key"),
    )
    with op.batch_alter_table("messaginginbox", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_messaginginbox_status"), ["status"], unique=False
        )

    op.create_table(
        "conversationbinding",
        sa.Column("connection_id", sa.String(), nullable=False),
        sa.Column("external_conversation_id", sa.String(), nullable=False),
        sa.Column("household_id", sa.Integer(), nullable=False),
        sa.Column("bound_by_user_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["bound_by_user_id"],
            ["user.id"],
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["messagingconnection.id"],
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
        ),
        sa.PrimaryKeyConstraint("connection_id", "external_conversation_id"),
    )
    op.create_table(
        "digestrun",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("delivery_id", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
        ),
        sa.PrimaryKeyConstraint("user_id", "local_date"),
    )
    op.create_table(
        "externalidentity",
        sa.Column("connection_id", sa.String(), nullable=False),
        sa.Column("external_user_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["messagingconnection.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
        ),
        sa.PrimaryKeyConstraint("connection_id", "external_user_id"),
    )
    with op.batch_alter_table("externalidentity", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_externalidentity_user_id"), ["user_id"], unique=False
        )

    op.create_table(
        "identitylink",
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("origin_connection_id", sa.String(), nullable=False),
        sa.Column("target_connection_id", sa.String(), nullable=True),
        sa.Column("target_external_user_id", sa.String(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["origin_connection_id"],
            ["messagingconnection.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
        ),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_table(
        "messagingaction",
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("connection_id", sa.String(), nullable=False),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("thread_id", sa.String(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("household_id", sa.Integer(), nullable=False),
        sa.Column("membership_generation", sa.Integer(), nullable=False),
        sa.Column("command", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["messagingconnection.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
        ),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_table(
        "messagingoutbox",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("delivery_key", sa.String(), nullable=False),
        sa.Column("connection_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("household_id", sa.Integer(), nullable=True),
        sa.Column("membership_generation", sa.Integer(), nullable=False),
        sa.Column("request_json", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("receipts_json", sa.String(), nullable=False),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["messagingconnection.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("delivery_key"),
    )
    with op.batch_alter_table("messagingoutbox", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_messagingoutbox_status"), ["status"], unique=False
        )

    op.create_table(
        "notificationdestination",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.String(), nullable=False),
        sa.Column("external_conversation_id", sa.String(), nullable=False),
        sa.Column("opted_in", sa.Boolean(), nullable=False),
        sa.Column("last_customer_message_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["messagingconnection.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("connection_id", "external_conversation_id"),
    )
    with op.batch_alter_table("notificationdestination", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_notificationdestination_user_id"), ["user_id"], unique=False
        )

    op.execute("""INSERT INTO messagingconnection
        (id, provider, label, settings_json, credential_source, enabled, state,
         revision, inbound_verified, created_at)
        VALUES ('legacy-telegram', 'telegram', 'Telegram', '{}', 'environment',
                0, 'unconfigured', 1, 0, CURRENT_TIMESTAMP)""")
    op.execute("""INSERT INTO externalidentity (connection_id, external_user_id, user_id)
        SELECT 'legacy-telegram', CAST(telegram_id AS TEXT), id FROM user""")
    op.execute("""INSERT INTO notificationdestination
        (id, user_id, connection_id, external_conversation_id, opted_in)
        SELECT 'legacy-' || id, id, 'legacy-telegram', CAST(chat_id AS TEXT), 1 FROM user""")
    op.execute("""INSERT INTO conversationbinding
        (connection_id, external_conversation_id, household_id, bound_by_user_id)
        SELECT 'legacy-telegram', CAST(chat_id AS TEXT), household_id, bound_by_user_id
        FROM groupbinding""")


def downgrade() -> None:
    incompatible = (
        op.get_bind()
        .execute(
            sa.text("""SELECT count(*) FROM user
        WHERE telegram_id IS NULL OR chat_id IS NULL OR id != telegram_id
           OR membership_active = 0""")
        )
        .scalar_one()
    )
    writes = (
        op.get_bind()
        .execute(
            sa.text("""SELECT count(*) FROM messaginginbox
        WHERE connection_id != 'legacy-telegram'""")
        )
        .scalar_one()
    )
    if incompatible or writes:
        raise RuntimeError(
            "Cannot downgrade after messaging identity writes; use a coordinated restore"
        )
    with op.batch_alter_table("notificationdestination", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_notificationdestination_user_id"))

    op.drop_table("notificationdestination")
    with op.batch_alter_table("messagingoutbox", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_messagingoutbox_status"))

    op.drop_table("messagingoutbox")
    op.drop_table("messagingaction")
    op.drop_table("identitylink")
    with op.batch_alter_table("externalidentity", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_externalidentity_user_id"))

    op.drop_table("externalidentity")
    op.drop_table("digestrun")
    op.drop_table("conversationbinding")
    with op.batch_alter_table("messaginginbox", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_messaginginbox_status"))

    op.drop_table("messaginginbox")
    op.drop_table("connectionoauthstate")
    op.drop_table("messagingoperation")
    op.drop_table("messagingconnection")
    op.drop_table("connectionadminsession")

    with op.batch_alter_table("groupbinding", naming_convention=NAMES) as batch:
        batch.drop_constraint(
            "fk_groupbinding_bound_by_user_id_user", type_="foreignkey"
        )
        batch.create_foreign_key(
            "fk_groupbinding_bound_by_user_id_user",
            "user",
            ["bound_by_user_id"],
            ["telegram_id"],
        )
    with op.batch_alter_table(
        "user", naming_convention=NAMES, recreate="always"
    ) as batch:
        batch.drop_constraint("pk_user", type_="primary")
        batch.drop_constraint("uq_user_telegram_id", type_="unique")
        batch.create_primary_key("pk_user", ["telegram_id"])
        batch.alter_column("telegram_id", existing_type=sa.Integer(), nullable=False)
        batch.alter_column("chat_id", existing_type=sa.Integer(), nullable=False)
        batch.drop_column("membership_generation")
        batch.drop_column("membership_active")
        batch.drop_column("id")
