"""Add typed race intelligence metadata and resolved battle summaries.

Revision ID: 20260901_0012
Revises: 20260810_0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260901_0012"
down_revision: str | None = "20260810_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "normalized_race_events",
        sa.Column(
            "event_origin",
            sa.String(length=20),
            server_default="SOURCE_FACT",
            nullable=False,
        ),
    )
    op.add_column(
        "normalized_race_events", sa.Column("primary_driver_number", sa.Integer(), nullable=True)
    )
    op.add_column(
        "normalized_race_events", sa.Column("secondary_driver_number", sa.Integer(), nullable=True)
    )
    op.add_column(
        "normalized_race_events", sa.Column("position_before", sa.Integer(), nullable=True)
    )
    op.add_column(
        "normalized_race_events", sa.Column("position_after", sa.Integer(), nullable=True)
    )
    op.add_column("normalized_race_events", sa.Column("gap_seconds", sa.Float(), nullable=True))
    op.add_column(
        "normalized_race_events", sa.Column("interval_seconds", sa.Float(), nullable=True)
    )
    op.add_column(
        "normalized_race_events",
        sa.Column("importance_level", sa.String(length=20), server_default="LOW", nullable=False),
    )
    op.add_column(
        "normalized_race_events",
        sa.Column("confidence_level", sa.String(length=20), server_default="HIGH", nullable=False),
    )
    op.add_column("normalized_race_events", sa.Column("derivation", sa.JSON(), nullable=True))
    op.execute(
        "UPDATE normalized_race_events SET event_origin = 'DERIVED' "
        "WHERE raw_event_id IS NULL AND source = 'apexarena'"
    )
    op.create_index(
        "ix_normalized_race_events_event_origin", "normalized_race_events", ["event_origin"]
    )
    op.create_index(
        "ix_normalized_race_events_primary_driver",
        "normalized_race_events",
        ["session_key", "primary_driver_number", "event_time"],
    )
    op.create_index(
        "ix_normalized_race_events_secondary_driver",
        "normalized_race_events",
        ["session_key", "secondary_driver_number", "event_time"],
    )
    op.create_index(
        "ix_normalized_race_events_importance_level",
        "normalized_race_events",
        ["session_key", "importance_level", "event_time"],
    )


def downgrade() -> None:
    op.drop_index("ix_normalized_race_events_importance_level", table_name="normalized_race_events")
    op.drop_index("ix_normalized_race_events_secondary_driver", table_name="normalized_race_events")
    op.drop_index("ix_normalized_race_events_primary_driver", table_name="normalized_race_events")
    op.drop_index("ix_normalized_race_events_event_origin", table_name="normalized_race_events")
    op.drop_column("normalized_race_events", "derivation")
    op.drop_column("normalized_race_events", "confidence_level")
    op.drop_column("normalized_race_events", "importance_level")
    op.drop_column("normalized_race_events", "interval_seconds")
    op.drop_column("normalized_race_events", "gap_seconds")
    op.drop_column("normalized_race_events", "position_after")
    op.drop_column("normalized_race_events", "position_before")
    op.drop_column("normalized_race_events", "secondary_driver_number")
    op.drop_column("normalized_race_events", "primary_driver_number")
    op.drop_column("normalized_race_events", "event_origin")
