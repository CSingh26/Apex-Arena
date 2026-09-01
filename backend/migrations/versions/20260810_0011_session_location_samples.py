"""Persist driver track positions outside the replay event sequence.

Revision ID: 20260810_0011
Revises: 20260808_0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260810_0011"
down_revision: str | None = "20260808_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "session_location_samples",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_key", sa.String(length=80), nullable=False),
        sa.Column("driver_number", sa.Integer(), nullable=False),
        sa.Column("sample_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("x", sa.Float(), nullable=False),
        sa.Column("y", sa.Float(), nullable=False),
        sa.Column("z", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="historical"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_key",
            "driver_number",
            "sample_time",
            name="uq_location_sample_session_driver_time",
        ),
    )
    op.create_index(
        "ix_session_location_samples_session_key",
        "session_location_samples",
        ["session_key"],
    )
    op.create_index(
        "ix_location_sample_session_time",
        "session_location_samples",
        ["session_key", "sample_time"],
    )

    op.create_table(
        "session_track_geometry",
        sa.Column("session_key", sa.String(length=80), nullable=False),
        sa.Column("min_x", sa.Float(), nullable=False),
        sa.Column("max_x", sa.Float(), nullable=False),
        sa.Column("min_y", sa.Float(), nullable=False),
        sa.Column("max_y", sa.Float(), nullable=False),
        sa.Column("path", sa.JSON(), nullable=False),
        sa.Column("source_driver_number", sa.Integer(), nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("session_key"),
    )


def downgrade() -> None:
    op.drop_table("session_track_geometry")
    op.drop_index("ix_location_sample_session_time", table_name="session_location_samples")
    op.drop_index(
        "ix_session_location_samples_session_key",
        table_name="session_location_samples",
    )
    op.drop_table("session_location_samples")
