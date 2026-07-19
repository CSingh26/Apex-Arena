"""Add resumable OpenF1 historical backfill jobs.

Revision ID: 20260719_0007
Revises: 20260718_0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260719_0007"
down_revision: str | None = "20260718_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "openf1_backfill_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("meeting_key", sa.String(80), nullable=True),
        sa.Column("session_key", sa.String(80), nullable=False),
        sa.Column("room_slug", sa.String(180), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column(
            "requested_endpoints",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "completed_endpoints",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("failed_endpoint", sa.String(40), nullable=True),
        sa.Column("rows_fetched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_inserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_deduplicated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_rejected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(80), nullable=True),
        sa.Column("last_error_message", sa.String(300), nullable=True),
        sa.Column(
            "cursor_state",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("season", "session_key", name="uq_openf1_backfill_season_session"),
    )
    op.create_index(
        "ix_openf1_backfill_status_updated",
        "openf1_backfill_jobs",
        ["status", "updated_at"],
    )
    op.create_index(
        "ix_openf1_backfill_jobs_meeting_key",
        "openf1_backfill_jobs",
        ["meeting_key"],
    )
    op.create_index(
        "ix_openf1_backfill_jobs_room_slug",
        "openf1_backfill_jobs",
        ["room_slug"],
    )
    op.create_index("ix_openf1_backfill_jobs_season", "openf1_backfill_jobs", ["season"])
    op.create_index(
        "ix_openf1_backfill_jobs_session_key",
        "openf1_backfill_jobs",
        ["session_key"],
    )


def downgrade() -> None:
    op.drop_table("openf1_backfill_jobs")
