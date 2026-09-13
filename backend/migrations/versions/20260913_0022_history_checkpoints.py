"""Immutable factual history revisions referenced by compact projections."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260913_0022"
down_revision: str | None = "20260913_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "session_history_checkpoints",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_key", sa.String(80), nullable=False),
        sa.Column("algorithm_version", sa.String(160), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("source_sequence", sa.Integer(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("encoded", sa.Text(), nullable=False),
        sa.Column("encoded_bytes", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_key",
            "algorithm_version",
            "schema_version",
            "source_sequence",
            name="uq_history_checkpoint_revision",
        ),
    )
    op.create_index(
        "ix_session_history_checkpoints_session_key", "session_history_checkpoints", ["session_key"]
    )
    op.add_column(
        "session_intelligence_progress",
        sa.Column("history_reference", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "session_intelligence_progress",
        sa.Column(
            "history_detail_status",
            sa.String(32),
            server_default="legacy_history_unverified",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("session_intelligence_progress", "history_detail_status")
    op.drop_column("session_intelligence_progress", "history_reference")
    op.drop_table("session_history_checkpoints")
