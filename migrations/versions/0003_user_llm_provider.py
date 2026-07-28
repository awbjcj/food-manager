"""user_llm_provider

Revision ID: 0003_user_llm_provider
Revises: 0002_pending_correction
Create Date: 2026-05-30
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_user_llm_provider"
down_revision: str | None = "0002_pending_correction"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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
