"""Track historical ingestion-run freshness for interrupted-worker recovery.

Revision ID: 20260912_0017
Revises: 20260912_0016
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260912_0017"
down_revision: str | Sequence[str] | None = "20260912_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ingestion_runs",
        sa.Column(
            "heartbeat_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    # Preserve NULL as "unknown" for legacy rows so startup falls back to their
    # actual started_at. Only runs inserted after this migration receive a fresh
    # initial heartbeat.
    op.alter_column(
        "ingestion_runs",
        "heartbeat_at",
        server_default=sa.func.now(),
    )


def downgrade() -> None:
    op.drop_column("ingestion_runs", "heartbeat_at")
