# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.domain.rooms import (
    AgentProfile,
    MessageEvidence,
    MessageTopic,
    RaceRoom,
    RoomMessage,
    RoomPlaybackState,
)


class RaceRoomListResponse(BaseModel):
    rooms: list[RaceRoom]
    total: int
    limit: int
    offset: int


class RaceRoomDetailResponse(BaseModel):
    room: RaceRoom
    agents: list[AgentProfile]
    playback: RoomPlaybackState
    data_notice: str


class RoomMessagesResponse(BaseModel):
    messages: list[RoomMessage]
    next_cursor: int | None


class MessageEvidenceResponse(BaseModel):
    message_id: UUID
    evidence: list[MessageEvidence]


class PlaybackRequest(BaseModel):
    action: Literal["pause", "resume", "seek", "speed"]
    playback_speed: float | None = Field(default=None, ge=0.25, le=8)
    sequence: int | None = Field(default=None, ge=0)
    lap_number: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_action_value(self) -> PlaybackRequest:
        if self.action == "speed" and self.playback_speed is None:
            raise ValueError("playback_speed is required for speed changes")
        if self.action == "seek" and self.sequence is None and self.lap_number is None:
            raise ValueError("sequence or lap_number is required when seeking")
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
    lap_from: int | None = Field(default=None, ge=0)
    lap_to: int | None = Field(default=None, ge=0)
