"""name_translation

Revision ID: 0009_name_translation
Revises: 0008_user_lang
Create Date: 2026-05-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0009_name_translation"
down_revision: Union[str, None] = "0008_user_lang"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "nametranslation",
        sa.Column("lang", sa.String(), nullable=False),
        sa.Column("source_text", sa.String(), nullable=False),
        sa.Column("translated_text", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("lang", "source_text"),
    )


def downgrade() -> None:
    op.drop_table("nametranslation")
