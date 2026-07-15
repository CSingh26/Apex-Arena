# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class RaceEventType(StrEnum):
    SESSION_START = "SESSION_START"
    RACE_START = "RACE_START"
    POSITION_CHANGE = "POSITION_CHANGE"
    OVERTAKE = "OVERTAKE"
    LEAD_CHANGE = "LEAD_CHANGE"
    PIT_STOP = "PIT_STOP"
    TYRE_CHANGE = "TYRE_CHANGE"
    SAFETY_CAR = "SAFETY_CAR"
    VIRTUAL_SAFETY_CAR = "VIRTUAL_SAFETY_CAR"
    RED_FLAG = "RED_FLAG"
    YELLOW_FLAG = "YELLOW_FLAG"
    PENALTY = "PENALTY"
    INVESTIGATION = "INVESTIGATION"
    WEATHER_CHANGE = "WEATHER_CHANGE"
    RETIREMENT = "RETIREMENT"
    FASTEST_LAP = "FASTEST_LAP"
    SESSION_FINISH = "SESSION_FINISH"


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
    topic: str
    payload: dict[str, Any]
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class NormalizedRaceEvent(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    meeting_id: UUID
    session_id: UUID | None = None
    event_type: RaceEventType
    occurred_at: datetime
    driver_id: UUID | None = None
    importance: float = Field(default=0.5, ge=0, le=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    source_event_id: UUID | None = None


class RaceStateSnapshot(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    meeting_id: UUID
    session_id: UUID | None = None
    sequence: int = 0
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    state: dict[str, Any] = Field(default_factory=dict)


class Room(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    meeting_id: UUID
    slug: str
    name: str
    lifecycle_status: RoomLifecycleStatus = RoomLifecycleStatus.SCHEDULED
    is_public: bool = True
