# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.database import Base

JSON_TYPE = JSON().with_variant(JSONB, "postgresql")


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SeasonRecord(Base, TimestampMixin):
    __tablename__ = "seasons"

    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    series: Mapped[str] = mapped_column(String(80), default="Formula racing")


class RaceMeetingRecord(Base, TimestampMixin):
    __tablename__ = "race_meetings"
    __table_args__ = (
        UniqueConstraint("season_year", "round_number", name="uq_meeting_season_round"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    season_year: Mapped[int] = mapped_column(ForeignKey("seasons.year"), index=True)
    round_number: Mapped[int] = mapped_column(Integer)
    provider_meeting_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    race_name: Mapped[str] = mapped_column(String(160))
    circuit_id: Mapped[str] = mapped_column(String(100))
    circuit_name: Mapped[str] = mapped_column(String(160))
    locality: Mapped[str] = mapped_column(String(100))
    country: Mapped[str] = mapped_column(String(100))
    race_date: Mapped[date] = mapped_column(Date)
    race_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lifecycle_status: Mapped[str] = mapped_column(String(30), default="scheduled")
    is_target: Mapped[bool] = mapped_column(Boolean, default=False)


class ConstructorRecord(Base, TimestampMixin):
    __tablename__ = "constructors"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    provider_id: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    nationality: Mapped[str | None] = mapped_column(String(80), nullable=True)


class DriverRecord(Base, TimestampMixin):
    __tablename__ = "drivers"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    provider_id: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    racing_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    code: Mapped[str | None] = mapped_column(String(5), nullable=True)
    given_name: Mapped[str] = mapped_column(String(80))
    family_name: Mapped[str] = mapped_column(String(80))
    constructor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("constructors.id"), nullable=True
    )


class RaceSessionRecord(Base, TimestampMixin):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    meeting_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("race_meetings.id"), index=True)
    provider_session_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    name: Mapped[str] = mapped_column(String(100))
    session_type: Mapped[str] = mapped_column(String(50))
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RoomRecord(Base, TimestampMixin):
    __tablename__ = "rooms"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    meeting_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("race_meetings.id"), index=True)
    slug: Mapped[str] = mapped_column(String(160), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    lifecycle_status: Mapped[str] = mapped_column(String(30), default="scheduled")
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)


class RawProviderEventRecord(Base):
    __tablename__ = "raw_provider_events"
    __table_args__ = (
        UniqueConstraint("deterministic_hash", name="uq_raw_provider_event_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(30), index=True)
    provider_event_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    provider_endpoint: Mapped[str] = mapped_column(String(120), index=True)
    deterministic_hash: Mapped[str] = mapped_column(String(64), index=True)
    session_key: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sessions.id"), nullable=True)
    event_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE)
    payload_hash: Mapped[str] = mapped_column(String(64), index=True)
    processing_status: Mapped[str] = mapped_column(String(30), default="pending", index=True)


class NormalizedRaceEventRecord(Base):
    __tablename__ = "normalized_race_events"
    __table_args__ = (
        UniqueConstraint("dedup_key", name="uq_normalized_event_dedup_key"),
        UniqueConstraint(
            "session_key", "sequence_number", name="uq_normalized_event_session_sequence"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("race_meetings.id"), nullable=True, index=True
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sessions.id"), nullable=True)
    session_key: Mapped[str] = mapped_column(String(80), index=True)
    source: Mapped[str] = mapped_column(String(30), index=True)
    raw_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("raw_provider_events.id"), nullable=True
    )
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    sequence_number: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    driver_numbers: Mapped[list[int]] = mapped_column(JSON_TYPE, default=list)
    lap_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    importance: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE)
    dedup_key: Mapped[str] = mapped_column(String(64), index=True)
    is_replay: Mapped[bool] = mapped_column(Boolean, default=False)


class RaceStateSnapshotRecord(Base):
    __tablename__ = "race_state_snapshots"
    __table_args__ = (
        UniqueConstraint("session_key", "sequence_number", name="uq_snapshot_session_sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("race_meetings.id"), nullable=True, index=True
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sessions.id"), nullable=True)
    session_key: Mapped[str] = mapped_column(String(80), index=True)
    snapshot_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    sequence_number: Mapped[int] = mapped_column(Integer)
    current_lap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    session_status: Mapped[str] = mapped_column(String(40), default="unknown")
    state: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IngestionRunRecord(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(30), index=True)
    session_key: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    run_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON_TYPE, default=dict)
    raw_inserted: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    normalized_inserted: Mapped[int] = mapped_column(Integer, default=0)
