"""user_llm_provider

Revision ID: 0003_user_llm_provider
Revises: 0002_pending_correction
Create Date: 2026-05-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_user_llm_provider"
down_revision: Union[str, None] = "0002_pending_correction"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column(
            "llm_provider",
            sa.String(),
            nullable=False,
            server_default="anthropic",
        ),
    )


def downgrade() -> None:
    op.drop_column("user", "llm_provider")
