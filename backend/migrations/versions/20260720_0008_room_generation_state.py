"""Add persisted room chat generation state.

Revision ID: 20260720_0008
Revises: 20260719_0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260720_0008"
down_revision: str | None = "20260719_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "race_rooms",
        sa.Column(
            "chat_generation_status",
            sa.String(30),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "race_rooms",
        sa.Column("generated_message_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "race_rooms",
        sa.Column("last_generated_sequence", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "race_rooms",
        sa.Column("generation_version", sa.String(80), nullable=False, server_default="rooms-v1"),
    )
    op.add_column("race_rooms", sa.Column("generation_error", sa.String(500), nullable=True))
    op.add_column(
        "race_rooms", sa.Column("generation_started_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "race_rooms",
        sa.Column("generation_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_race_rooms_chat_generation_status", "race_rooms", ["chat_generation_status"]
    )

    op.add_column("room_messages", sa.Column("generation_key", sa.String(160), nullable=True))
    op.add_column(
        "room_messages",
        sa.Column("generation_version", sa.String(80), nullable=False, server_default="rooms-v1"),
    )
    op.add_column("room_messages", sa.Column("source_provider", sa.String(40), nullable=True))
    op.add_column("room_messages", sa.Column("source_reference", sa.String(180), nullable=True))
    op.add_column(
        "room_messages",
        sa.Column(
            "generation_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "room_messages",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_unique_constraint(
        "uq_room_message_generation_key", "room_messages", ["room_id", "generation_key"]
    )
    op.create_index(
        "ix_room_messages_generation_version", "room_messages", ["room_id", "generation_version"]
    )
    op.create_index(
        "ix_room_messages_source_reference",
        "room_messages",
        ["source_provider", "source_reference"],
    )
    op.create_index("ix_room_messages_archived_at", "room_messages", ["archived_at"])


def downgrade() -> None:
    op.drop_index("ix_room_messages_archived_at", table_name="room_messages")
    op.drop_index("ix_room_messages_source_reference", table_name="room_messages")
    op.drop_index("ix_room_messages_generation_version", table_name="room_messages")
    op.drop_constraint("uq_room_message_generation_key", "room_messages", type_="unique")
    op.drop_column("room_messages", "archived_at")
    op.drop_column("room_messages", "generation_metadata")
    op.drop_column("room_messages", "source_reference")
    op.drop_column("room_messages", "source_provider")
    op.drop_column("room_messages", "generation_version")
    op.drop_column("room_messages", "generation_key")

    op.drop_index("ix_race_rooms_chat_generation_status", table_name="race_rooms")
    op.drop_column("race_rooms", "generation_completed_at")
    op.drop_column("race_rooms", "generation_started_at")
    op.drop_column("race_rooms", "generation_error")
    op.drop_column("race_rooms", "generation_version")
    op.drop_column("race_rooms", "last_generated_sequence")
    op.drop_column("race_rooms", "generated_message_count")
    op.drop_column("race_rooms", "chat_generation_status")
