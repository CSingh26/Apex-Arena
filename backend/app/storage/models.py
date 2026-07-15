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
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.database import Base


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
        UniqueConstraint("provider", "provider_event_id", name="uq_raw_provider_event"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(30), index=True)
    provider_event_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    topic: Mapped[str] = mapped_column(String(120), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class NormalizedRaceEventRecord(Base):
    __tablename__ = "normalized_race_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    meeting_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("race_meetings.id"), index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sessions.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    driver_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("drivers.id"), nullable=True)
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("raw_provider_events.id"), nullable=True
    )


class RaceStateSnapshotRecord(Base):
    __tablename__ = "race_state_snapshots"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence", name="uq_snapshot_session_sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    meeting_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("race_meetings.id"), index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sessions.id"), nullable=True)
    sequence: Mapped[int] = mapped_column(Integer)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    state: Mapped[dict[str, Any]] = mapped_column(JSON)
