# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import hmac
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from app.api.room_schemas import (
    MessageEvidenceResponse,
    PlaybackRequest,
    RaceRoomDetailResponse,
    RaceRoomListResponse,
    ReplayResponse,
    RoomGenerationResponse,
    RoomMessagesResponse,
)
from app.domain.rooms import MessageTopic, RoomStatus, SourceAvailability
from app.services.container import AppServices

router = APIRouter(prefix="/api/v1/race-rooms", tags=["Race Rooms"])


def get_services(request: Request) -> AppServices:
    return request.app.state.services


Services = Annotated[AppServices, Depends(get_services)]


async def require_room(slug: str, services: AppServices):
    await services.rooms.ensure_catalog()
    room = await services.room_repository.get_room(slug)
    if room is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Race room not found")
    return room


def require_internal_key(services: AppServices, supplied: str | None) -> None:
    configured = services.settings.internal_api_key
    if configured is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal room generation is not configured",
        )
    if supplied is None or not hmac.compare_digest(supplied, configured.get_secret_value()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal key")


@router.get("", response_model=RaceRoomListResponse)
async def list_race_rooms(
    services: Services,
    season: int | None = Query(default=None, ge=2023, le=2100),
    room_status: Annotated[RoomStatus | None, Query(alias="status")] = None,
    search: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> RaceRoomListResponse:
    await services.rooms.ensure_catalog()
    rooms, total = await services.room_repository.list_rooms(
        season=season,
        status=room_status,
        search=search,
        limit=limit,
        offset=offset,
    )
    return RaceRoomListResponse(rooms=rooms, total=total, limit=limit, offset=offset)


@router.get("/{room_slug}", response_model=RaceRoomDetailResponse)
async def race_room_detail(room_slug: str, services: Services) -> RaceRoomDetailResponse:
    room = await require_room(room_slug, services)
    agents = await services.room_repository.get_agents(room.id)
    playback = await services.room_repository.get_playback(room.id)
    notices = {
        SourceAvailability.TELEMETRY: "Detailed normalized telemetry is available.",
        SourceAvailability.LIMITED: "Some telemetry is incomplete; conclusions are qualified.",
        SourceAvailability.RESULTS_ONLY: (
            "Race metadata is available. Detailed telemetry discussion has not been generated."
        ),
        SourceAvailability.UNAVAILABLE: "Telemetry is not available for this room yet.",
    }
    return RaceRoomDetailResponse(
        room=room,
        agents=agents,
        playback=playback,
        data_notice=notices[room.source_availability],
    )


@router.get("/{room_slug}/messages", response_model=RoomMessagesResponse)
async def room_messages(
    room_slug: str,
    services: Services,
    after_sequence: int = Query(default=0, ge=0),
    agent_id: str | None = Query(default=None, max_length=80),
    topic: Annotated[MessageTopic | None, Query()] = None,
    lap_from: int | None = Query(default=None, ge=0),
    lap_to: int | None = Query(default=None, ge=0),
    limit: int = Query(default=100, ge=1, le=250),
) -> RoomMessagesResponse:
    room = await require_room(room_slug, services)
    messages = await services.room_repository.list_messages(
        room.id,
        after_sequence=after_sequence,
        agent_id=agent_id,
        topic=topic,
        lap_from=lap_from,
        lap_to=lap_to,
        limit=limit,
    )
    return RoomMessagesResponse(
        messages=messages,
        next_cursor=messages[-1].sequence if len(messages) == limit else None,
    )


@router.get(
    "/{room_slug}/messages/{message_id}/evidence",
    response_model=MessageEvidenceResponse,
)
async def message_evidence(
    room_slug: str, message_id: UUID, services: Services
) -> MessageEvidenceResponse:
    await require_room(room_slug, services)
    evidence = await services.room_repository.message_evidence(message_id)
    return MessageEvidenceResponse(message_id=message_id, evidence=evidence)


@router.post("/{room_slug}/replay", response_model=ReplayResponse)
async def start_replay(room_slug: str, services: Services) -> ReplayResponse:
    room = await require_room(room_slug, services)
    if room.status in {RoomStatus.UNAVAILABLE, RoomStatus.FAILED}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Replay is unavailable")
    playback = await services.room_repository.update_playback(
        room.id, current_sequence=0, playback_speed=1, is_paused=False
    )
    return ReplayResponse(room=room, playback=playback)


@router.post("/{room_slug}/playback", response_model=ReplayResponse)
async def change_playback(
    room_slug: str, payload: PlaybackRequest, services: Services
) -> ReplayResponse:
    room = await require_room(room_slug, services)
    values: dict[str, int | float | bool] = {}
    if payload.action == "pause":
        values["is_paused"] = True
    elif payload.action == "resume":
        values["is_paused"] = False
    elif payload.action == "speed":
        values["playback_speed"] = payload.playback_speed or 1
    elif payload.action == "seek":
        values["current_sequence"] = (
            payload.sequence
            or await services.room_repository.sequence_for_lap(
                room.id, payload.lap_number or 0
            )
        )
    playback = await services.room_repository.update_playback(room.id, **values)
    return ReplayResponse(room=room, playback=playback)


@router.post("/{room_slug}/generate", response_model=RoomGenerationResponse)
async def generate_room(
    room_slug: str,
    services: Services,
    internal_api_key: Annotated[str | None, Header(alias="X-Internal-API-Key")] = None,
) -> RoomGenerationResponse:
    require_internal_key(services, internal_api_key)
    room = await require_room(room_slug, services)
    if room.session_key is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No normalized session is linked to this room",
        )
    events = await services.normalized_event_repository.list_for_session(
        room.session_key, after_sequence=0, limit=1000
    )
    for event in events:
        await services.room_discussion.consume(event)
    messages = await services.room_repository.list_messages(room.id, limit=1)
    refreshed = await services.room_repository.get_room(room_slug)
    return RoomGenerationResponse(
        room_slug=room_slug,
        events_evaluated=len(events),
        messages_available=refreshed.message_count if refreshed else len(messages),
    )
