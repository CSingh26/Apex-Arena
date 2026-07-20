# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.domain.circuits import CircuitIntelligence, SessionWeather
from app.domain.rooms import (
    AgentProfile,
    EventWeekend,
    MessageEvidence,
    MessageTopic,
    MessageType,
    RaceRoom,
    RoomMessage,
    RoomPlaybackState,
)


class RaceRoomListResponse(BaseModel):
    rooms: list[RaceRoom]
    total: int
    limit: int
    offset: int


class EventWeekendListResponse(BaseModel):
    events: list[EventWeekend]
    total: int
    limit: int
    offset: int


class RaceRoomDetailResponse(BaseModel):
    room: RaceRoom
    agents: list[AgentProfile]
    playback: RoomPlaybackState
    circuit: CircuitIntelligence
    weather: SessionWeather
    data_notice: str
    diagnostics_available: bool = False


class RoomMessagesResponse(BaseModel):
    messages: list[RoomMessage]
    next_cursor: int | None


class RoomGenerationStatusResponse(BaseModel):
    room_slug: str
    status: str
    generation_version: str
    generated_message_count: int
    last_generated_sequence: int
    generation_error: str | None = None
    generation_started_at: str | None = None
    generation_completed_at: str | None = None


class MessageEvidenceResponse(BaseModel):
    message_id: UUID
    evidence: list[MessageEvidence]
    trigger_event: dict[str, Any] | None = None
    snapshot_reference: str | None = None
    data_quality_flags: list[str] = Field(default_factory=list)
    generation_mode: str
    confidence: str


class ReplayRequest(BaseModel):
    action: Literal["start", "restart", "resume"] = "start"


class PlaybackRequest(BaseModel):
    action: Literal[
        "pause",
        "resume",
        "seek_to_lap",
        "seek_to_phase",
        "seek_to_sequence",
        "seek_to_session_time",
        "set_speed",
    ]
    playback_speed: Literal[0.5, 1.0, 2.0, 4.0, 8.0] | None = None
    sequence: int | None = Field(default=None, ge=0)
    lap_number: int | None = Field(default=None, ge=0)
    phase: str | None = Field(default=None, min_length=2, max_length=4)
    session_time: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_action_value(self) -> PlaybackRequest:
        if self.action == "set_speed" and self.playback_speed is None:
            raise ValueError("playback_speed is required for speed changes")
        if self.action == "seek_to_sequence" and self.sequence is None:
            raise ValueError("sequence is required when seeking by sequence")
        if self.action == "seek_to_lap" and self.lap_number is None:
            raise ValueError("lap_number is required when seeking by lap")
        if self.action == "seek_to_phase" and self.phase is None:
            raise ValueError("phase is required when seeking by qualifying phase")
        if self.action == "seek_to_session_time" and self.session_time is None:
            raise ValueError("session_time is required when seeking by session time")
        return self


class ReplayResponse(BaseModel):
    room: RaceRoom
    playback: RoomPlaybackState


class RoomGenerationResponse(BaseModel):
    room_slug: str
    events_evaluated: int
    messages_available: int


class RoomMessageFilters(BaseModel):
    agent_id: str | None = None
    topic: MessageTopic | None = None
    message_type: MessageType | None = None
    lap_from: int | None = Field(default=None, ge=0)
    lap_to: int | None = Field(default=None, ge=0)


class RoomDiagnosticsResponse(BaseModel):
    room_slug: str
    raw_event_count: int
    normalized_event_count: int
    snapshot_count: int
    latest_event_sequence: int
    ordering_buffer_pending: int
    stream_state: str
    provider_mode: str
    connection_state: str
    latest_events: list[dict[str, Any]]
    race_state: dict[str, Any]
    playback: RoomPlaybackState
    discussion: dict[str, int]
