"""Create Day 1 race-data foundation.

Revision ID: 20260715_0001
Revises: None

SPDX-License-Identifier: AGPL-3.0-only
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260715_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "seasons",
        sa.Column("year", sa.Integer(), primary_key=True),
        sa.Column("series", sa.String(length=80), nullable=False),
        *timestamps(),
    )
    op.create_table(
        "constructors",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider_id", sa.String(length=100), unique=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("nationality", sa.String(length=80)),
        *timestamps(),
    )
    op.create_index("ix_constructors_name", "constructors", ["name"])
    op.create_table(
        "race_meetings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("season_year", sa.Integer(), sa.ForeignKey("seasons.year"), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("provider_meeting_key", sa.String(length=80)),
        sa.Column("race_name", sa.String(length=160), nullable=False),
        sa.Column("circuit_id", sa.String(length=100), nullable=False),
        sa.Column("circuit_name", sa.String(length=160), nullable=False),
        sa.Column("locality", sa.String(length=100), nullable=False),
        sa.Column("country", sa.String(length=100), nullable=False),
        sa.Column("race_date", sa.Date(), nullable=False),
        sa.Column("race_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lifecycle_status", sa.String(length=30), nullable=False),
        sa.Column("is_target", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("season_year", "round_number", name="uq_meeting_season_round"),
        *timestamps(),
    )
    op.create_index("ix_race_meetings_season_year", "race_meetings", ["season_year"])
    op.create_table(
        "drivers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider_id", sa.String(length=100), unique=True),
        sa.Column("racing_number", sa.Integer()),
        sa.Column("code", sa.String(length=5)),
        sa.Column("given_name", sa.String(length=80), nullable=False),
        sa.Column("family_name", sa.String(length=80), nullable=False),
        sa.Column("constructor_id", sa.Uuid(), sa.ForeignKey("constructors.id")),
        *timestamps(),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("meeting_id", sa.Uuid(), sa.ForeignKey("race_meetings.id"), nullable=False),
        sa.Column("provider_session_key", sa.String(length=80)),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("session_type", sa.String(length=50), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True)),
        sa.Column("ends_at", sa.DateTime(timezone=True)),
        *timestamps(),
    )
    op.create_index("ix_sessions_meeting_id", "sessions", ["meeting_id"])
    op.create_table(
        "rooms",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("meeting_id", sa.Uuid(), sa.ForeignKey("race_meetings.id"), nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False, unique=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("lifecycle_status", sa.String(length=30), nullable=False),
        sa.Column("is_public", sa.Boolean(), nullable=False),
        *timestamps(),
    )
    op.create_index("ix_rooms_meeting_id", "rooms", ["meeting_id"])
    op.create_table(
        "raw_provider_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("provider_event_id", sa.String(length=160)),
        sa.Column("topic", sa.String(length=120), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "provider_event_id", name="uq_raw_provider_event"),
    )
    op.create_index("ix_raw_provider_events_provider", "raw_provider_events", ["provider"])
    op.create_index(
        "ix_raw_provider_events_provider_event_id", "raw_provider_events", ["provider_event_id"]
    )
    op.create_index("ix_raw_provider_events_topic", "raw_provider_events", ["topic"])
    op.create_index("ix_raw_provider_events_received_at", "raw_provider_events", ["received_at"])
    op.create_table(
        "normalized_race_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("meeting_id", sa.Uuid(), sa.ForeignKey("race_meetings.id"), nullable=False),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("sessions.id")),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("driver_id", sa.Uuid(), sa.ForeignKey("drivers.id")),
        sa.Column("importance", sa.Float(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("source_event_id", sa.Uuid(), sa.ForeignKey("raw_provider_events.id")),
    )
    op.create_index(
        "ix_normalized_race_events_meeting_id", "normalized_race_events", ["meeting_id"]
    )
    op.create_index(
        "ix_normalized_race_events_event_type", "normalized_race_events", ["event_type"]
    )
    op.create_index(
        "ix_normalized_race_events_occurred_at", "normalized_race_events", ["occurred_at"]
    )
    op.create_table(
        "race_state_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("meeting_id", sa.Uuid(), sa.ForeignKey("race_meetings.id"), nullable=False),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("sessions.id")),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.UniqueConstraint("session_id", "sequence", name="uq_snapshot_session_sequence"),
    )
    op.create_index("ix_race_state_snapshots_meeting_id", "race_state_snapshots", ["meeting_id"])
    op.create_index("ix_race_state_snapshots_captured_at", "race_state_snapshots", ["captured_at"])


def downgrade() -> None:
    op.drop_table("race_state_snapshots")
    op.drop_table("normalized_race_events")
    op.drop_table("raw_provider_events")
    op.drop_table("rooms")
    op.drop_table("sessions")
    op.drop_table("drivers")
    op.drop_table("race_meetings")
    op.drop_table("constructors")
    op.drop_table("seasons")
