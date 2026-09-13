"""Durable critical-intelligence progress; legacy baselines are lazy and unverified."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260912_0019"
down_revision: str | None = "20260912_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "session_intelligence_progress",
        sa.Column("session_key", sa.String(80), primary_key=True),
        sa.Column("algorithm_version", sa.String(100), nullable=False),
        sa.Column("completed_source_id", sa.Uuid(), nullable=True),
        sa.Column("completed_source_sequence", sa.BigInteger(), nullable=False),
        sa.Column("completed_through_sequence", sa.BigInteger(), nullable=False),
        sa.Column("pending_source_id", sa.Uuid(), nullable=True),
        sa.Column("pending_source_sequence", sa.BigInteger(), nullable=True),
        sa.Column("historical_effects_unverified", sa.Boolean(), nullable=False),
        sa.Column("failure_code", sa.String(80), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_session_intelligence_progress_pending_source_id",
        "session_intelligence_progress",
        ["pending_source_id"],
    )


def downgrade() -> None:
    op.drop_table("session_intelligence_progress")
