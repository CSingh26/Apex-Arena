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
    op.create_table(
        "battle_summaries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("battle_key", sa.String(length=180), nullable=False),
        sa.Column("session_key", sa.String(length=80), nullable=False),
        sa.Column("lead_driver_number", sa.Integer(), nullable=False),
        sa.Column("chasing_driver_number", sa.Integer(), nullable=False),
        sa.Column("lead_position", sa.Integer(), nullable=False),
        sa.Column("chasing_position", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closest_interval_seconds", sa.Float(), nullable=False),
        sa.Column("peak_intensity", sa.String(length=20), nullable=False),
        sa.Column("outcome", sa.String(length=80), nullable=True),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("battle_key", name="uq_battle_summaries_battle_key"),
    )
    op.create_index("ix_battle_summaries_battle_key", "battle_summaries", ["battle_key"])
    op.create_index("ix_battle_summaries_session_key", "battle_summaries", ["session_key"])
    op.create_index(
        "ix_battle_summaries_lead_driver", "battle_summaries", ["lead_driver_number"]
    )
    op.create_index(
        "ix_battle_summaries_chasing_driver", "battle_summaries", ["chasing_driver_number"]
    )
    op.create_index("ix_battle_summaries_started_at", "battle_summaries", ["started_at"])
    op.create_index("ix_battle_summaries_ended_at", "battle_summaries", ["ended_at"])


def downgrade() -> None:
    op.drop_index("ix_battle_summaries_ended_at", table_name="battle_summaries")
    op.drop_index("ix_battle_summaries_started_at", table_name="battle_summaries")
    op.drop_index("ix_battle_summaries_chasing_driver", table_name="battle_summaries")
    op.drop_index("ix_battle_summaries_lead_driver", table_name="battle_summaries")
    op.drop_index("ix_battle_summaries_session_key", table_name="battle_summaries")
    op.drop_index("ix_battle_summaries_battle_key", table_name="battle_summaries")
    op.drop_table("battle_summaries")
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
