# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator


class EventOrigin(StrEnum):
    SOURCE_FACT = "SOURCE_FACT"
    DERIVED = "DERIVED"


class EventImportance(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    IMPORTANT = "IMPORTANT"
    MAJOR = "MAJOR"
    CRITICAL = "CRITICAL"


class EventConfidence(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class DerivationEvidence(BaseModel):
    kind: str
    observed_at: datetime
    event_id: UUID | None = None
    value: str | int | float | bool | None = None


class EventDerivation(BaseModel):
    algorithm: str
    version: int = Field(default=1, ge=1)
    evidence: list[DerivationEvidence] = Field(default_factory=list)
    exclusions_checked: list[str] = Field(default_factory=list)


class RaceEventType(StrEnum):
    SESSION_START = "SESSION_START"
    SESSION_PHASE_CHANGE = "SESSION_PHASE_CHANGE"
    SESSION_END = "SESSION_END"
    SESSION_STATUS = "SESSION_STATUS"
    QUALIFYING_PHASE = "QUALIFYING_PHASE"
    RACE_START = "RACE_START"
    DRIVER_UPDATE = "DRIVER_UPDATE"
    POSITION_SAMPLE = "POSITION_SAMPLE"
    INTERVAL_SAMPLE = "INTERVAL_SAMPLE"
    LAP_COMPLETED = "LAP_COMPLETED"
    POSITION_CHANGE = "POSITION_CHANGE"
    POSITION_GAIN = "POSITION_GAIN"
    POSITION_LOSS = "POSITION_LOSS"
    OVERTAKE = "OVERTAKE"
    LEAD_CHANGE = "LEAD_CHANGE"
    PIT_STOP = "PIT_STOP"
    PIT_ENTRY = "PIT_ENTRY"
    PIT_EXIT = "PIT_EXIT"
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
    CAR_DATA_SAMPLE = "CAR_DATA_SAMPLE"
    LOCATION_SAMPLE = "LOCATION_SAMPLE"
    RETIREMENT = "RETIREMENT"
    DRIVER_STOPPED = "DRIVER_STOPPED"
    DRIVER_RETIRED = "DRIVER_RETIRED"
    FASTEST_LAP = "FASTEST_LAP"
    PERSONAL_BEST = "PERSONAL_BEST"
    BATTLE_STARTED = "BATTLE_STARTED"
    BATTLE_INTENSIFIED = "BATTLE_INTENSIFIED"
    BATTLE_ENDED = "BATTLE_ENDED"
    DRS_RANGE_ENTERED = "DRS_RANGE_ENTERED"
    DRS_RANGE_EXITED = "DRS_RANGE_EXITED"
    QUALIFYING_CUTOFF_CHANGE = "QUALIFYING_CUTOFF_CHANGE"
    ELIMINATION_RISK = "ELIMINATION_RISK"
    PROVISIONAL_POLE = "PROVISIONAL_POLE"
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
    ends_at: datetime | None = None


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
    event_origin: EventOrigin = EventOrigin.SOURCE_FACT
    primary_driver_number: int | None = None
    secondary_driver_number: int | None = None
    position_before: int | None = None
    position_after: int | None = None
    gap_seconds: float | None = None
    interval_seconds: float | None = None
    lap_number: int | None = None
    importance: float | None = Field(default=None, ge=0, le=1)
    importance_level: EventImportance = EventImportance.LOW
    confidence: float | None = Field(default=None, ge=0, le=1)
    confidence_level: EventConfidence = EventConfidence.HIGH
    derivation: EventDerivation | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    dedup_key: str
    is_replay: bool = False

    @model_validator(mode="after")
    def populate_driver_numbers(self) -> NormalizedRaceEvent:
        if not self.driver_numbers:
            self.driver_numbers = [
                number
                for number in (self.primary_driver_number, self.secondary_driver_number)
                if number is not None
            ]
        return self


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
