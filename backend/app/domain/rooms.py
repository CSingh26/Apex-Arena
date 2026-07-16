# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class RoomStatus(StrEnum):
    PENDING = "pending"
    INGESTING = "ingesting"
    READY = "ready"
    REPLAYING = "replaying"
    COMPLETED = "completed"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class RoomMode(StrEnum):
    LIVE = "live"
    REPLAY = "replay"
    ARCHIVED = "archived"
    DEVELOPMENT = "development"


class SourceAvailability(StrEnum):
    TELEMETRY = "telemetry"
    LIMITED = "limited_telemetry"
    RESULTS_ONLY = "results_only"
    UNAVAILABLE = "unavailable"


class MessageTopic(StrEnum):
    STRATEGY = "strategy"
    PACE = "pace"
    RACECRAFT = "racecraft"
    INCIDENT = "incident"
    PIT_STOP = "pit_stop"
    TYRES = "tyres"
    CHAMPIONSHIP = "championship"
    SUMMARY = "summary"
    SESSION = "session"


class MessageType(StrEnum):
    OBSERVATION = "observation"
    ANALYSIS = "analysis"
    QUESTION = "question"
    AGREEMENT = "agreement"
    DISAGREEMENT = "disagreement"
    CORRECTION = "correction"
    SUMMARY = "summary"
    UNCERTAINTY = "uncertainty_notice"


class EvidenceStatus(StrEnum):
    GROUNDED = "grounded"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AgentProfile(BaseModel):
    id: str
    name: str
    role: str
    description: str
    avatar_key: str
    specialties: list[str]
    personality: list[str]
    style_rules: list[str]
    speaking_style: str
    supported_topics: list[MessageTopic]
    accent: str
    enabled: bool = True
    sort_order: int


class RaceRoom(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    slug: str
    session_key: str | None = None
    season: int
    round_number: int | None = None
    race_name: str
    official_name: str
    circuit_name: str
    country: str
    session_type: str = "Race"
    scheduled_start: datetime
    actual_start: datetime | None = None
    status: RoomStatus
    mode: RoomMode
    current_lap: int | None = None
    total_laps: int | None = None
    source_availability: SourceAvailability
    message_count: int = 0
    agent_count: int = 0
    last_event_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    is_featured: bool = False
    is_development: bool = False


class RaceRoomAgent(BaseModel):
    room_id: UUID
    agent_id: str
    is_active: bool = True
    joined_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    left_at: datetime | None = None
    sort_order: int


class RoomMessage(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    room_id: UUID
    agent_id: str
    sequence: int
    lap_number: int | None = None
    session_time: float | None = None
    wall_time: datetime | None = None
    topic: MessageTopic
    message_type: MessageType
    content: str
    confidence: Confidence = Confidence.MEDIUM
    evidence_status: EvidenceStatus = EvidenceStatus.PARTIAL
    reply_to_message_id: UUID | None = None
    trigger_event_id: UUID | None = None
    trigger_snapshot_id: UUID | None = None
    generated_by: str = "deterministic"
    model_name: str | None = None
    prompt_version: str = "rooms-v1"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MessageEvidence(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    message_id: UUID
    evidence_type: str
    source_provider: str
    source_reference: str
    metric_name: str | None = None
    metric_value: str | float | int | None = None
    unit: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RoomPlaybackState(BaseModel):
    room_id: UUID
    current_sequence: int = 0
    playback_speed: float = Field(default=1.0, ge=0.25, le=8)
    is_paused: bool = True
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
