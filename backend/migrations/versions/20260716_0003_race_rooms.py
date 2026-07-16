"""Add persistent Race Rooms discussions.

Revision ID: 20260716_0003
Revises: 20260716_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260716_0003"
down_revision: str | None = "20260716_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_profiles",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("role", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("avatar_key", sa.String(20), nullable=False),
        sa.Column("specialties", sa.JSON(), nullable=False),
        sa.Column("personality", sa.JSON(), nullable=False),
        sa.Column("style_rules", sa.JSON(), nullable=False),
        sa.Column("speaking_style", sa.Text(), nullable=False),
        sa.Column("supported_topics", sa.JSON(), nullable=False),
        sa.Column("accent", sa.String(30), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "race_rooms",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("slug", sa.String(180), nullable=False, unique=True),
        sa.Column("session_key", sa.String(80)),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("round_number", sa.Integer()),
        sa.Column("race_name", sa.String(180), nullable=False),
        sa.Column("official_name", sa.String(180), nullable=False),
        sa.Column("circuit_name", sa.String(180), nullable=False),
        sa.Column("country", sa.String(100), nullable=False),
        sa.Column("session_type", sa.String(60), nullable=False),
        sa.Column("scheduled_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actual_start", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("mode", sa.String(30), nullable=False),
        sa.Column("current_lap", sa.Integer()),
        sa.Column("total_laps", sa.Integer()),
        sa.Column("source_availability", sa.String(40), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("agent_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_event_at", sa.DateTime(timezone=True)),
        sa.Column("is_featured", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_development", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    for column in ("slug", "session_key", "season", "scheduled_start", "status", "mode"):
        op.create_index(f"ix_race_rooms_{column}", "race_rooms", [column])
    op.create_table(
        "race_room_agents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("room_id", sa.Uuid(), sa.ForeignKey("race_rooms.id"), nullable=False),
        sa.Column("agent_id", sa.String(80), sa.ForeignKey("agent_profiles.id"), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("left_at", sa.DateTime(timezone=True)),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.UniqueConstraint("room_id", "agent_id", name="uq_room_agent"),
    )
    op.create_index("ix_race_room_agents_room_id", "race_room_agents", ["room_id"])
    op.create_index("ix_race_room_agents_agent_id", "race_room_agents", ["agent_id"])
    op.create_table(
        "room_messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("room_id", sa.Uuid(), sa.ForeignKey("race_rooms.id"), nullable=False),
        sa.Column("agent_id", sa.String(80), sa.ForeignKey("agent_profiles.id"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("lap_number", sa.Integer()),
        sa.Column("session_time", sa.Float()),
        sa.Column("wall_time", sa.DateTime(timezone=True)),
        sa.Column("topic", sa.String(40), nullable=False),
        sa.Column("message_type", sa.String(40), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("confidence", sa.String(20), nullable=False),
        sa.Column("evidence_status", sa.String(30), nullable=False),
        sa.Column("reply_to_message_id", sa.Uuid(), sa.ForeignKey("room_messages.id")),
        sa.Column("trigger_event_id", sa.Uuid(), sa.ForeignKey("normalized_race_events.id")),
        sa.Column("trigger_snapshot_id", sa.Uuid(), sa.ForeignKey("race_state_snapshots.id")),
        sa.Column("generated_by", sa.String(40), nullable=False),
        sa.Column("model_name", sa.String(100)),
        sa.Column("prompt_version", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("room_id", "sequence", name="uq_room_message_sequence"),
        sa.UniqueConstraint(
            "room_id", "trigger_event_id", "agent_id", name="uq_room_trigger_agent"
        ),
    )
    for column in ("room_id", "agent_id", "lap_number", "topic", "trigger_event_id"):
        op.create_index(f"ix_room_messages_{column}", "room_messages", [column])
    op.create_table(
        "message_evidence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("message_id", sa.Uuid(), sa.ForeignKey("room_messages.id"), nullable=False),
        sa.Column("evidence_type", sa.String(40), nullable=False),
        sa.Column("source_provider", sa.String(40), nullable=False),
        sa.Column("source_reference", sa.String(180), nullable=False),
        sa.Column("metric_name", sa.String(100)),
        sa.Column("metric_value", sa.String(180)),
        sa.Column("unit", sa.String(40)),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_message_evidence_message_id", "message_evidence", ["message_id"])
    op.create_table(
        "room_playback_states",
        sa.Column("room_id", sa.Uuid(), sa.ForeignKey("race_rooms.id"), primary_key=True),
        sa.Column("current_sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("playback_speed", sa.Float(), nullable=False, server_default="1"),
        sa.Column("is_paused", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("room_playback_states")
    op.drop_index("ix_message_evidence_message_id", table_name="message_evidence")
    op.drop_table("message_evidence")
    for column in ("trigger_event_id", "topic", "lap_number", "agent_id", "room_id"):
        op.drop_index(f"ix_room_messages_{column}", table_name="room_messages")
    op.drop_table("room_messages")
    op.drop_index("ix_race_room_agents_agent_id", table_name="race_room_agents")
    op.drop_index("ix_race_room_agents_room_id", table_name="race_room_agents")
    op.drop_table("race_room_agents")
    for column in ("mode", "status", "scheduled_start", "season", "session_key", "slug"):
        op.drop_index(f"ix_race_rooms_{column}", table_name="race_rooms")
    op.drop_table("race_rooms")
    op.drop_table("agent_profiles")
