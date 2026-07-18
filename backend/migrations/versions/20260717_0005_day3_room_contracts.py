"""Expand the durable Day 3 Race Rooms contracts.

Revision ID: 20260717_0005
Revises: 20260716_0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_0005"
down_revision: str | None = "20260716_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("agent_profiles", "name", new_column_name="display_name")
    op.alter_column("agent_profiles", "description", new_column_name="short_description")
    op.alter_column("agent_profiles", "personality", new_column_name="personality_rules")
    op.alter_column("agent_profiles", "accent", new_column_name="ui_accent_key")
    op.alter_column("agent_profiles", "enabled", new_column_name="active")
    op.drop_column("agent_profiles", "style_rules")

    op.add_column("race_rooms", sa.Column("country_code", sa.String(3), nullable=True))
    op.add_column(
        "race_rooms",
        sa.Column("telemetry_quality", sa.String(30), nullable=False, server_default="unknown"),
    )
    op.create_index("ix_race_rooms_season_round", "race_rooms", ["season", "round_number"])

    op.add_column(
        "message_evidence",
        sa.Column("evidence_key", sa.String(100), nullable=False, server_default="event_type"),
    )
    op.create_index("ix_room_messages_room_lap", "room_messages", ["room_id", "lap_number"])
    op.create_index("ix_room_messages_room_agent", "room_messages", ["room_id", "agent_id"])
    op.create_index("ix_room_messages_created_at", "room_messages", ["created_at"])

    op.alter_column(
        "room_playback_states", "current_sequence", new_column_name="current_event_sequence"
    )
    op.add_column(
        "room_playback_states",
        sa.Column("current_message_sequence", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("room_playback_states", sa.Column("current_lap", sa.Integer(), nullable=True))
    op.add_column(
        "room_playback_states",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("room_playback_states", "started_at")
    op.drop_column("room_playback_states", "current_lap")
    op.drop_column("room_playback_states", "current_message_sequence")
    op.alter_column(
        "room_playback_states", "current_event_sequence", new_column_name="current_sequence"
    )
    op.drop_index("ix_room_messages_created_at", table_name="room_messages")
    op.drop_index("ix_room_messages_room_agent", table_name="room_messages")
    op.drop_index("ix_room_messages_room_lap", table_name="room_messages")
    op.drop_column("message_evidence", "evidence_key")
    op.drop_index("ix_race_rooms_season_round", table_name="race_rooms")
    op.drop_column("race_rooms", "telemetry_quality")
    op.drop_column("race_rooms", "country_code")
    op.add_column(
        "agent_profiles",
        sa.Column("style_rules", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.alter_column("agent_profiles", "active", new_column_name="enabled")
    op.alter_column("agent_profiles", "ui_accent_key", new_column_name="accent")
    op.alter_column("agent_profiles", "personality_rules", new_column_name="personality")
    op.alter_column("agent_profiles", "short_description", new_column_name="description")
    op.alter_column("agent_profiles", "display_name", new_column_name="name")
