"""Use active-row uniqueness for generated room messages.

Revision ID: 20260720_0009
Revises: 20260720_0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260720_0009"
down_revision: str | None = "20260720_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _assert_no_active_duplicates() -> None:
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
              IF EXISTS (
                SELECT 1
                FROM room_messages
                WHERE archived_at IS NULL
                  AND trigger_event_id IS NOT NULL
                GROUP BY room_id, trigger_event_id, agent_id
                HAVING count(*) > 1
              ) THEN
                RAISE EXCEPTION
                  'active duplicate room/trigger/agent rows block message uniqueness migration';
              END IF;

              IF EXISTS (
                SELECT 1
                FROM room_messages
                WHERE archived_at IS NULL
                  AND generation_key IS NOT NULL
                GROUP BY room_id, generation_key
                HAVING count(*) > 1
              ) THEN
                RAISE EXCEPTION
                  'active duplicate generation_key rows block message uniqueness migration';
              END IF;
            END
            $$;
            """
        )
    )


def _assert_legacy_constraints_can_be_restored() -> None:
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
              IF EXISTS (
                SELECT 1
                FROM room_messages
                WHERE trigger_event_id IS NOT NULL
                GROUP BY room_id, trigger_event_id, agent_id
                HAVING count(*) > 1
              ) THEN
                RAISE EXCEPTION
                  'all-row duplicate room/trigger/agent rows block downgrade';
              END IF;

              IF EXISTS (
                SELECT 1
                FROM room_messages
                WHERE generation_key IS NOT NULL
                GROUP BY room_id, generation_key
                HAVING count(*) > 1
              ) THEN
                RAISE EXCEPTION
                  'all-row duplicate generation_key rows block downgrade';
              END IF;
            END
            $$;
            """
        )
    )


def upgrade() -> None:
    _assert_no_active_duplicates()
    op.drop_constraint("uq_room_trigger_agent", "room_messages", type_="unique")
    op.drop_constraint("uq_room_message_generation_key", "room_messages", type_="unique")
    op.create_index(
        "uq_room_message_active_trigger_agent",
        "room_messages",
        ["room_id", "trigger_event_id", "agent_id"],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )
    op.create_index(
        "uq_room_message_active_generation_key",
        "room_messages",
        ["room_id", "generation_key"],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL AND generation_key IS NOT NULL"),
    )


def downgrade() -> None:
    _assert_legacy_constraints_can_be_restored()
    op.drop_index("uq_room_message_active_generation_key", table_name="room_messages")
    op.drop_index("uq_room_message_active_trigger_agent", table_name="room_messages")
    op.create_unique_constraint(
        "uq_room_message_generation_key", "room_messages", ["room_id", "generation_key"]
    )
    op.create_unique_constraint(
        "uq_room_trigger_agent",
        "room_messages",
        ["room_id", "trigger_event_id", "agent_id"],
    )
