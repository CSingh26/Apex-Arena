"""Expand event persistence for the unified Day 2 race engine.

Revision ID: 20260716_0002
Revises: 20260715_0001

SPDX-License-Identifier: AGPL-3.0-only
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260716_0002"
down_revision: str | None = "20260715_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade_raw_events() -> None:
    op.drop_constraint("uq_raw_provider_event", "raw_provider_events", type_="unique")
    op.drop_index("ix_raw_provider_events_topic", table_name="raw_provider_events")
    op.alter_column("raw_provider_events", "topic", new_column_name="provider_endpoint")
    op.alter_column("raw_provider_events", "payload", new_column_name="raw_payload")
    op.alter_column(
        "raw_provider_events",
        "raw_payload",
        type_=postgresql.JSONB(astext_type=sa.Text()),
        postgresql_using="raw_payload::jsonb",
    )
    op.add_column("raw_provider_events", sa.Column("deterministic_hash", sa.String(64)))
    op.add_column("raw_provider_events", sa.Column("session_key", sa.String(80)))
    op.add_column(
        "raw_provider_events",
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("sessions.id")),
    )
    op.add_column("raw_provider_events", sa.Column("event_time", sa.DateTime(timezone=True)))
    op.add_column("raw_provider_events", sa.Column("payload_hash", sa.String(64)))
    op.add_column(
        "raw_provider_events",
        sa.Column("processing_status", sa.String(30), server_default="pending"),
    )
    op.execute("UPDATE raw_provider_events SET payload_hash = md5(raw_payload::text)")
    op.execute(
        """
        UPDATE raw_provider_events
        SET deterministic_hash = md5(
            provider || ':' || provider_endpoint || ':' ||
            coalesce(provider_event_id, '') || ':' || payload_hash
        )
        """
    )
    op.alter_column("raw_provider_events", "deterministic_hash", nullable=False)
    op.alter_column("raw_provider_events", "payload_hash", nullable=False)
    op.alter_column("raw_provider_events", "processing_status", nullable=False)
    op.alter_column("raw_provider_events", "processing_status", server_default=None)
    op.create_unique_constraint(
        "uq_raw_provider_event_hash", "raw_provider_events", ["deterministic_hash"]
    )
    op.create_index(
        "ix_raw_provider_events_provider_endpoint",
        "raw_provider_events",
        ["provider_endpoint"],
    )
    op.create_index(
        "ix_raw_provider_events_deterministic_hash",
        "raw_provider_events",
        ["deterministic_hash"],
    )
    op.create_index("ix_raw_provider_events_session_key", "raw_provider_events", ["session_key"])
    op.create_index("ix_raw_provider_events_event_time", "raw_provider_events", ["event_time"])
    op.create_index("ix_raw_provider_events_payload_hash", "raw_provider_events", ["payload_hash"])
    op.create_index(
        "ix_raw_provider_events_processing_status",
        "raw_provider_events",
        ["processing_status"],
    )


def upgrade_normalized_events() -> None:
    op.drop_index(
        "ix_normalized_race_events_occurred_at", table_name="normalized_race_events"
    )
    op.alter_column(
        "normalized_race_events", "source_event_id", new_column_name="raw_event_id"
    )
    op.alter_column("normalized_race_events", "occurred_at", new_column_name="event_time")
    op.alter_column("normalized_race_events", "meeting_id", nullable=True)
    op.alter_column("normalized_race_events", "importance", nullable=True)
    op.alter_column(
        "normalized_race_events",
        "payload",
        type_=postgresql.JSONB(astext_type=sa.Text()),
        postgresql_using="payload::jsonb",
    )
    op.add_column("normalized_race_events", sa.Column("session_key", sa.String(80)))
    op.add_column("normalized_race_events", sa.Column("source", sa.String(30)))
    op.add_column(
        "normalized_race_events", sa.Column("received_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "normalized_race_events",
        sa.Column("processed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.add_column("normalized_race_events", sa.Column("sequence_number", sa.Integer()))
    op.add_column(
        "normalized_race_events",
        sa.Column(
            "driver_numbers",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column("normalized_race_events", sa.Column("lap_number", sa.Integer()))
    op.add_column("normalized_race_events", sa.Column("confidence", sa.Float()))
    op.add_column("normalized_race_events", sa.Column("dedup_key", sa.String(64)))
    op.add_column(
        "normalized_race_events",
        sa.Column("is_replay", sa.Boolean(), server_default=sa.false()),
    )
    op.execute(
        """
        UPDATE normalized_race_events AS event
        SET session_key = coalesce(session.provider_session_key, 'unknown')
        FROM sessions AS session
        WHERE event.session_id = session.id
        """
    )
    op.execute(
        "UPDATE normalized_race_events SET session_key = 'unknown' WHERE session_key IS NULL"
    )
    op.execute(
        """
        UPDATE normalized_race_events AS event
        SET driver_numbers = jsonb_build_array(driver.racing_number)
        FROM drivers AS driver
        WHERE event.driver_id = driver.id AND driver.racing_number IS NOT NULL
        """
    )
    op.execute(
        """
        WITH ordered AS (
            SELECT id, row_number() OVER (
                PARTITION BY session_key ORDER BY event_time, id
            ) AS sequence_number
            FROM normalized_race_events
        )
        UPDATE normalized_race_events AS event
        SET sequence_number = ordered.sequence_number
        FROM ordered
        WHERE event.id = ordered.id
        """
    )
    op.execute(
        """
        UPDATE normalized_race_events
        SET source = 'openf1',
            received_at = event_time,
            dedup_key = md5(
                session_key || ':' || event_type || ':' || event_time::text || ':' || payload::text
            )
        """
    )
    op.alter_column("normalized_race_events", "session_key", nullable=False)
    op.alter_column("normalized_race_events", "source", nullable=False)
    op.alter_column("normalized_race_events", "received_at", nullable=False)
    op.alter_column("normalized_race_events", "processed_at", nullable=False)
    op.alter_column("normalized_race_events", "sequence_number", nullable=False)
    op.alter_column("normalized_race_events", "driver_numbers", nullable=False)
    op.alter_column("normalized_race_events", "driver_numbers", server_default=None)
    op.alter_column("normalized_race_events", "dedup_key", nullable=False)
    op.alter_column("normalized_race_events", "is_replay", nullable=False)
    op.alter_column("normalized_race_events", "is_replay", server_default=None)
    op.drop_constraint(
        "normalized_race_events_driver_id_fkey", "normalized_race_events", type_="foreignkey"
    )
    op.drop_column("normalized_race_events", "driver_id")
    op.create_unique_constraint(
        "uq_normalized_event_dedup_key", "normalized_race_events", ["dedup_key"]
    )
    op.create_unique_constraint(
        "uq_normalized_event_session_sequence",
        "normalized_race_events",
        ["session_key", "sequence_number"],
    )
    op.create_index(
        "ix_normalized_race_events_event_time", "normalized_race_events", ["event_time"]
    )
    op.create_index(
        "ix_normalized_race_events_session_key", "normalized_race_events", ["session_key"]
    )
    op.create_index("ix_normalized_race_events_source", "normalized_race_events", ["source"])
    op.create_index(
        "ix_normalized_race_events_dedup_key", "normalized_race_events", ["dedup_key"]
    )


def upgrade_snapshots() -> None:
    op.drop_constraint(
        "uq_snapshot_session_sequence", "race_state_snapshots", type_="unique"
    )
    op.drop_index("ix_race_state_snapshots_captured_at", table_name="race_state_snapshots")
    op.alter_column("race_state_snapshots", "sequence", new_column_name="sequence_number")
    op.alter_column("race_state_snapshots", "captured_at", new_column_name="snapshot_time")
    op.alter_column("race_state_snapshots", "meeting_id", nullable=True)
    op.alter_column(
        "race_state_snapshots",
        "state",
        type_=postgresql.JSONB(astext_type=sa.Text()),
        postgresql_using="state::jsonb",
    )
    op.add_column("race_state_snapshots", sa.Column("session_key", sa.String(80)))
    op.add_column("race_state_snapshots", sa.Column("current_lap", sa.Integer()))
    op.add_column(
        "race_state_snapshots",
        sa.Column("session_status", sa.String(40), server_default="unknown"),
    )
    op.add_column(
        "race_state_snapshots",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.execute(
        """
        UPDATE race_state_snapshots AS snapshot
        SET session_key = coalesce(session.provider_session_key, 'unknown')
        FROM sessions AS session
        WHERE snapshot.session_id = session.id
        """
    )
    op.execute("UPDATE race_state_snapshots SET session_key = 'unknown' WHERE session_key IS NULL")
    op.alter_column("race_state_snapshots", "session_key", nullable=False)
    op.alter_column("race_state_snapshots", "session_status", nullable=False)
    op.alter_column("race_state_snapshots", "session_status", server_default=None)
    op.alter_column("race_state_snapshots", "created_at", nullable=False)
    op.create_unique_constraint(
        "uq_snapshot_session_sequence",
        "race_state_snapshots",
        ["session_key", "sequence_number"],
    )
    op.create_index(
        "ix_race_state_snapshots_session_key", "race_state_snapshots", ["session_key"]
    )
    op.create_index(
        "ix_race_state_snapshots_snapshot_time", "race_state_snapshots", ["snapshot_time"]
    )


def upgrade_ingestion_runs() -> None:
    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("session_key", sa.String(80)),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("last_event_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(500)),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("raw_inserted", sa.Integer(), nullable=False),
        sa.Column("duplicates", sa.Integer(), nullable=False),
        sa.Column("normalized_inserted", sa.Integer(), nullable=False),
    )
    op.create_index("ix_ingestion_runs_provider", "ingestion_runs", ["provider"])
    op.create_index("ix_ingestion_runs_session_key", "ingestion_runs", ["session_key"])
    op.create_index("ix_ingestion_runs_status", "ingestion_runs", ["status"])


def upgrade() -> None:
    upgrade_raw_events()
    upgrade_normalized_events()
    upgrade_snapshots()
    upgrade_ingestion_runs()


def downgrade() -> None:
    op.drop_table("ingestion_runs")

    op.drop_index("ix_race_state_snapshots_snapshot_time", table_name="race_state_snapshots")
    op.drop_index("ix_race_state_snapshots_session_key", table_name="race_state_snapshots")
    op.drop_constraint(
        "uq_snapshot_session_sequence", "race_state_snapshots", type_="unique"
    )
    op.drop_column("race_state_snapshots", "created_at")
    op.drop_column("race_state_snapshots", "session_status")
    op.drop_column("race_state_snapshots", "current_lap")
    op.drop_column("race_state_snapshots", "session_key")
    op.alter_column(
        "race_state_snapshots",
        "state",
        type_=sa.JSON(),
        postgresql_using="state::json",
    )
    op.alter_column("race_state_snapshots", "meeting_id", nullable=False)
    op.alter_column("race_state_snapshots", "snapshot_time", new_column_name="captured_at")
    op.alter_column("race_state_snapshots", "sequence_number", new_column_name="sequence")
    op.create_unique_constraint(
        "uq_snapshot_session_sequence", "race_state_snapshots", ["session_id", "sequence"]
    )
    op.create_index(
        "ix_race_state_snapshots_captured_at", "race_state_snapshots", ["captured_at"]
    )

    op.drop_index("ix_normalized_race_events_dedup_key", table_name="normalized_race_events")
    op.drop_index("ix_normalized_race_events_source", table_name="normalized_race_events")
    op.drop_index("ix_normalized_race_events_session_key", table_name="normalized_race_events")
    op.drop_index("ix_normalized_race_events_event_time", table_name="normalized_race_events")
    op.drop_constraint(
        "uq_normalized_event_session_sequence", "normalized_race_events", type_="unique"
    )
    op.drop_constraint(
        "uq_normalized_event_dedup_key", "normalized_race_events", type_="unique"
    )
    op.add_column(
        "normalized_race_events",
        sa.Column("driver_id", sa.Uuid(), sa.ForeignKey("drivers.id")),
    )
    op.drop_column("normalized_race_events", "is_replay")
    op.drop_column("normalized_race_events", "dedup_key")
    op.drop_column("normalized_race_events", "confidence")
    op.drop_column("normalized_race_events", "lap_number")
    op.drop_column("normalized_race_events", "driver_numbers")
    op.drop_column("normalized_race_events", "sequence_number")
    op.drop_column("normalized_race_events", "processed_at")
    op.drop_column("normalized_race_events", "received_at")
    op.drop_column("normalized_race_events", "source")
    op.drop_column("normalized_race_events", "session_key")
    op.alter_column(
        "normalized_race_events", "payload", type_=sa.JSON(), postgresql_using="payload::json"
    )
    op.alter_column("normalized_race_events", "meeting_id", nullable=False)
    op.alter_column("normalized_race_events", "importance", nullable=False)
    op.alter_column("normalized_race_events", "event_time", new_column_name="occurred_at")
    op.alter_column("normalized_race_events", "raw_event_id", new_column_name="source_event_id")
    op.create_index(
        "ix_normalized_race_events_occurred_at", "normalized_race_events", ["occurred_at"]
    )

    op.drop_index("ix_raw_provider_events_processing_status", table_name="raw_provider_events")
    op.drop_index("ix_raw_provider_events_payload_hash", table_name="raw_provider_events")
    op.drop_index("ix_raw_provider_events_event_time", table_name="raw_provider_events")
    op.drop_index("ix_raw_provider_events_session_key", table_name="raw_provider_events")
    op.drop_index("ix_raw_provider_events_deterministic_hash", table_name="raw_provider_events")
    op.drop_index("ix_raw_provider_events_provider_endpoint", table_name="raw_provider_events")
    op.drop_constraint("uq_raw_provider_event_hash", "raw_provider_events", type_="unique")
    op.drop_column("raw_provider_events", "processing_status")
    op.drop_column("raw_provider_events", "payload_hash")
    op.drop_column("raw_provider_events", "event_time")
    op.drop_column("raw_provider_events", "session_id")
    op.drop_column("raw_provider_events", "session_key")
    op.drop_column("raw_provider_events", "deterministic_hash")
    op.alter_column(
        "raw_provider_events",
        "raw_payload",
        type_=sa.JSON(),
        postgresql_using="raw_payload::json",
    )
    op.alter_column("raw_provider_events", "raw_payload", new_column_name="payload")
    op.alter_column("raw_provider_events", "provider_endpoint", new_column_name="topic")
    op.create_index("ix_raw_provider_events_topic", "raw_provider_events", ["topic"])
    op.create_unique_constraint(
        "uq_raw_provider_event", "raw_provider_events", ["provider", "provider_event_id"]
    )
