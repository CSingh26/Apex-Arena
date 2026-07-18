"""Generalize race rooms to competitive session rooms without renaming tables.

Revision ID: 20260718_0006
Revises: 20260717_0005

``race_rooms`` is intentionally retained so existing room/message foreign keys and
generated discussions survive the Day 4 upgrade. New identity and lifecycle fields
make each row session-generic.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_0006"
down_revision: str | None = "20260717_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("race_rooms", sa.Column("event_slug", sa.String(180), nullable=True))
    op.add_column("race_rooms", sa.Column("meeting_key", sa.String(80), nullable=True))
    op.add_column(
        "race_rooms", sa.Column("weekend_start", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("race_rooms", sa.Column("weekend_end", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "race_rooms",
        sa.Column("is_sprint_weekend", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "race_rooms",
        sa.Column(
            "eligibility_status",
            sa.String(30),
            nullable=False,
            server_default="provider_pending",
        ),
    )
    op.add_column(
        "race_rooms",
        sa.Column("ingestion_status", sa.String(30), nullable=False, server_default="pending"),
    )
    op.add_column(
        "race_rooms",
        sa.Column("replay_available", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "race_rooms",
        sa.Column("results_available", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    # Existing Day 3 rows are Sunday-race rooms. Normalize their identity in-place;
    # message, evidence, and playback foreign keys are untouched.
    op.execute(
        """
        UPDATE race_rooms
        SET session_type = CASE
            WHEN lower(replace(session_type, '_', ' ')) IN
                 ('sprint qualifying', 'sprint shootout') THEN 'SPRINT_QUALIFYING'
            WHEN lower(replace(session_type, '_', ' ')) IN ('sprint', 'sprint race')
                 THEN 'SPRINT'
            WHEN lower(replace(session_type, '_', ' ')) IN ('qualifying', 'qualification')
                 THEN 'QUALIFYING'
            ELSE 'RACE'
        END
        """
    )
    op.execute(
        """
        UPDATE race_rooms
        SET event_slug = regexp_replace(
                slug,
                '-(sprint-qualifying|sprint|qualifying|race)$',
                ''
            ),
            weekend_start = scheduled_start,
            weekend_end = scheduled_start + interval '4 hours',
            is_sprint_weekend = session_type IN ('SPRINT_QUALIFYING', 'SPRINT'),
            ingestion_status = CASE
                WHEN message_count = 0 THEN 'pending'
                ELSE 'ready'
            END,
            replay_available = message_count > 0 AND source_availability IN
                ('telemetry', 'limited_telemetry', 'timing_only'),
            results_available = message_count > 0 AND status IN
                ('ready', 'completed', 'replaying', 'paused'),
            eligibility_status = CASE
                WHEN status = 'pending' AND scheduled_start > now() THEN 'future_read_only'
                WHEN status = 'live' THEN 'eligible_live'
                WHEN message_count > 0 AND source_availability <> 'unavailable'
                    THEN 'eligible_historical'
                ELSE 'provider_pending'
            END,
            source_availability = CASE
                WHEN message_count = 0 THEN 'unavailable'
                ELSE source_availability
            END,
            status = CASE
                WHEN message_count = 0 THEN 'pending'
                ELSE status
            END,
            mode = CASE
                WHEN message_count = 0 THEN 'replay'
                ELSE mode
            END
        """
    )
    op.alter_column("race_rooms", "event_slug", existing_type=sa.String(180), nullable=False)

    # OpenF1 session keys are globally unique. If a pre-Day-4 heuristic linked
    # one key to multiple empty rooms, retain the oldest/highest-value link and
    # mark the others unresolved instead of deleting any room or discussion.
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY session_key
                       ORDER BY message_count DESC, created_at ASC, id ASC
                   ) AS position
            FROM race_rooms
            WHERE session_key IS NOT NULL
        )
        UPDATE race_rooms
        SET session_key = NULL,
            eligibility_status = 'provider_pending',
            ingestion_status = 'pending',
            source_availability = 'unavailable',
            replay_available = false,
            results_available = false
        FROM ranked
        WHERE race_rooms.id = ranked.id AND ranked.position > 1
        """
    )
    op.create_unique_constraint(
        "uq_race_rooms_event_session",
        "race_rooms",
        ["season", "round_number", "session_type"],
    )
    op.create_unique_constraint("uq_race_rooms_provider_session_key", "race_rooms", ["session_key"])
    op.create_index("ix_race_rooms_event_slug", "race_rooms", ["event_slug"])
    op.create_index("ix_race_rooms_meeting_key", "race_rooms", ["meeting_key"])
    op.create_index("ix_race_rooms_session_type", "race_rooms", ["session_type"])
    op.create_index("ix_race_rooms_eligibility_status", "race_rooms", ["eligibility_status"])
    op.create_index("ix_race_rooms_ingestion_status", "race_rooms", ["ingestion_status"])


def downgrade() -> None:
    op.drop_index("ix_race_rooms_ingestion_status", table_name="race_rooms")
    op.drop_index("ix_race_rooms_eligibility_status", table_name="race_rooms")
    op.drop_index("ix_race_rooms_session_type", table_name="race_rooms")
    op.drop_index("ix_race_rooms_meeting_key", table_name="race_rooms")
    op.drop_index("ix_race_rooms_event_slug", table_name="race_rooms")
    op.drop_constraint("uq_race_rooms_provider_session_key", "race_rooms", type_="unique")
    op.drop_constraint("uq_race_rooms_event_session", "race_rooms", type_="unique")
    op.drop_column("race_rooms", "results_available")
    op.drop_column("race_rooms", "replay_available")
    op.drop_column("race_rooms", "ingestion_status")
    op.drop_column("race_rooms", "eligibility_status")
    op.drop_column("race_rooms", "is_sprint_weekend")
    op.drop_column("race_rooms", "weekend_end")
    op.drop_column("race_rooms", "weekend_start")
    op.drop_column("race_rooms", "meeting_key")
    op.drop_column("race_rooms", "event_slug")
