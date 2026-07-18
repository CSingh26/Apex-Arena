# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class RaceEventType(StrEnum):
    SESSION_START = "SESSION_START"
    SESSION_STATUS = "SESSION_STATUS"
    QUALIFYING_PHASE = "QUALIFYING_PHASE"
    RACE_START = "RACE_START"
    DRIVER_UPDATE = "DRIVER_UPDATE"
    POSITION_SAMPLE = "POSITION_SAMPLE"
    INTERVAL_SAMPLE = "INTERVAL_SAMPLE"
    LAP_COMPLETED = "LAP_COMPLETED"
    POSITION_CHANGE = "POSITION_CHANGE"
    OVERTAKE = "OVERTAKE"
    LEAD_CHANGE = "LEAD_CHANGE"
    PIT_STOP = "PIT_STOP"
    STINT_UPDATE = "STINT_UPDATE"
    TYRE_CHANGE = "TYRE_CHANGE"
    RACE_CONTROL = "RACE_CONTROL"
    SAFETY_CAR = "SAFETY_CAR"
    VIRTUAL_SAFETY_CAR = "VIRTUAL_SAFETY_CAR"
    RED_FLAG = "RED_FLAG"
    YELLOW_FLAG = "YELLOW_FLAG"
    PENALTY = "PENALTY"
    INVESTIGATION = "INVESTIGATION"
    WEATHER_CHANGE = "WEATHER_CHANGE"
    WEATHER_UPDATE = "WEATHER_UPDATE"
    RETIREMENT = "RETIREMENT"
    FASTEST_LAP = "FASTEST_LAP"
    LAP_DELETED = "LAP_DELETED"
    SESSION_RESULT = "SESSION_RESULT"
    STARTING_GRID = "STARTING_GRID"
    SESSION_FINISH = "SESSION_FINISH"
    UNKNOWN_PROVIDER_EVENT = "UNKNOWN_PROVIDER_EVENT"


class RoomLifecycleStatus(StrEnum):
    SCHEDULED = "scheduled"
    LIVE = "live"
    REPLAY_READY = "replay_ready"
    ARCHIVED = "archived"
    DEGRADED = "degraded"


class MeetingLifecycleStatus(StrEnum):
    COMPLETED = "completed"
    UPCOMING = "upcoming"
    LIVE = "live"


class Season(BaseModel):
    year: int = 2026
    series: str = "Formula racing"
    meetings: list[RaceMeeting] = Field(default_factory=list)


class RaceWeekendSession(BaseModel):
    name: str
    starts_at: datetime


class RaceMeeting(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    season_year: int
    round_number: int
    race_name: str
    circuit_id: str
    circuit_name: str
    locality: str
    country: str
    race_date: date
    race_start: datetime
    status: MeetingLifecycleStatus
    is_target: bool = False
    source_url: str | None = None
    sessions: list[RaceWeekendSession] = Field(default_factory=list)


class Session(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    meeting_id: UUID
    provider_session_key: str | None = None
    name: str
    session_type: str
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class Constructor(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    provider_id: str | None = None
    name: str
    nationality: str | None = None


class Driver(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    provider_id: str | None = None
    racing_number: int | None = None
    code: str | None = None
    given_name: str
    family_name: str
    constructor_id: UUID | None = None


class RawProviderEvent(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    provider: str
    provider_event_id: str | None = None
    provider_endpoint: str
    deterministic_hash: str
    session_key: str | None = None
    event_time: datetime | None = None
    raw_payload: dict[str, Any]
    payload_hash: str
    processing_status: str = "pending"
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class NormalizedRaceEvent(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    meeting_id: UUID | None = None
    session_id: UUID | None = None
    session_key: str
    source: str
    raw_event_id: UUID | None = None
    event_time: datetime
    received_at: datetime
    processed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    sequence_number: int = 0
    event_type: RaceEventType
    driver_numbers: list[int] = Field(default_factory=list)
    lap_number: int | None = None
    importance: float | None = Field(default=None, ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    dedup_key: str
    is_replay: bool = False


class RaceStateSnapshot(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    meeting_id: UUID | None = None
    session_id: UUID | None = None
    session_key: str
    snapshot_time: datetime = Field(default_factory=lambda: datetime.now(UTC))
    sequence_number: int = 0
    current_lap: int | None = None
    session_status: str = "unknown"
    state: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Room(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    meeting_id: UUID
    slug: str
    name: str
    lifecycle_status: RoomLifecycleStatus = RoomLifecycleStatus.SCHEDULED
    is_public: bool = True
