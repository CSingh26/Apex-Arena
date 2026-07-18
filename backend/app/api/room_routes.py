# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import hmac
import logging
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.api.room_schemas import (
    MessageEvidenceResponse,
    PlaybackRequest,
    RaceRoomDetailResponse,
    RaceRoomListResponse,
    ReplayRequest,
    ReplayResponse,
    RoomDiagnosticsResponse,
    RoomGenerationResponse,
    RoomMessagesResponse,
)
from app.api.room_streaming import race_room_stream
from app.domain.rooms import (
    MessageTopic,
    MessageType,
    RoomMode,
    RoomStatus,
    SourceAvailability,
)
from app.services.container import AppServices
from app.services.room_replay import ReplayUnavailableError

logger = logging.getLogger(__name__)
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
    mode: Annotated[RoomMode | None, Query()] = None,
    search: str | None = Query(default=None, max_length=100),
    sort: Literal["race_date_desc", "race_date_asc", "latest_activity"] = "race_date_desc",
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> RaceRoomListResponse:
    await services.rooms.ensure_catalog()
    rooms, total = await services.room_repository.list_rooms(
        season=season,
        status=room_status,
        mode=mode,
        search=search,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    return RaceRoomListResponse(rooms=rooms, total=total, limit=limit, offset=offset)


@router.post("/sync", response_model=dict[str, int])
async def sync_race_room_catalog(
    services: Services,
    internal_api_key: Annotated[str | None, Header(alias="X-Internal-API-Key")] = None,
) -> dict[str, int]:
    """Refresh the public calendar without exposing provider or deployment secrets."""
    require_internal_key(services, internal_api_key)
    try:
        synchronized = await services.rooms.force_sync()
    except Exception as exc:
        logger.warning("Race room catalog sync failed error=%s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Race room metadata providers are temporarily unavailable",
        ) from exc
    return {"rooms_synchronized": synchronized}


@router.get("/{room_slug}", response_model=RaceRoomDetailResponse)
async def race_room_detail(room_slug: str, services: Services) -> RaceRoomDetailResponse:
    room = await require_room(room_slug, services)
    agents = await services.room_repository.get_agents(room.id)
    playback = await services.room_repository.get_playback(room.id)
    notices = {
        SourceAvailability.TELEMETRY: "Detailed normalized telemetry is available.",
        SourceAvailability.LIMITED: "Some telemetry is incomplete; conclusions are qualified.",
        SourceAvailability.TIMING_ONLY: (
            "Timing data is available; telemetry-dependent conclusions are limited."
        ),
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
        diagnostics_available=(
            services.settings.app_env != "production" or services.settings.room_diagnostics_enabled
        ),
    )


@router.get("/{room_slug}/messages", response_model=RoomMessagesResponse)
async def room_messages(
    room_slug: str,
    services: Services,
    after_sequence: int = Query(default=0, ge=0),
    agent_id: str | None = Query(default=None, max_length=80),
    topic: Annotated[MessageTopic | None, Query()] = None,
    message_type: Annotated[MessageType | None, Query()] = None,
    lap_from: int | None = Query(default=None, ge=0),
    lap_to: int | None = Query(default=None, ge=0),
    sequence_from: Annotated[int | None, Query(ge=1)] = None,
    sequence_to: int | None = Query(default=None, ge=0),
    limit: int = Query(default=100, ge=1, le=250),
) -> RoomMessagesResponse:
    room = await require_room(room_slug, services)
    messages = await services.room_repository.list_messages(
        room.id,
        after_sequence=after_sequence,
        agent_id=agent_id,
        topic=topic,
        message_type=message_type,
        lap_from=lap_from,
        lap_to=lap_to,
        sequence_from=sequence_from,
        sequence_to=sequence_to,
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
    room = await require_room(room_slug, services)
    message = await services.room_repository.get_message(room.id, message_id)
    if message is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room message not found")
    evidence = await services.room_repository.message_evidence(message_id)
    quality_flags = sorted(
        {
            str(item.context.get("data_quality"))
            for item in evidence
            if item.context.get("data_quality")
        }
    )
    trigger_event = next(
        (
            {
                "event_id": item.source_reference,
                "event_sequence": item.context.get("event_sequence"),
                "lap_number": item.context.get("lap_number"),
                "source_provider": item.source_provider,
            }
            for item in evidence
        ),
        None,
    )
    return MessageEvidenceResponse(
        message_id=message_id,
        evidence=evidence,
        trigger_event=trigger_event,
        snapshot_reference=(
            str(message.trigger_snapshot_id) if message.trigger_snapshot_id is not None else None
        ),
        data_quality_flags=quality_flags,
        generation_mode=message.generated_by,
        confidence=message.confidence.value,
    )


@router.post("/{room_slug}/replay", response_model=ReplayResponse)
async def start_replay(
    room_slug: str,
    services: Services,
    payload: ReplayRequest | None = None,
) -> ReplayResponse:
    room = await require_room(room_slug, services)
    action = payload.action if payload is not None else "start"
    try:
        if action == "resume":
            playback = await services.room_replay.resume(room)
        else:
            playback = await services.room_replay.start(
                room,
                restart=action == "restart",
            )
    except ReplayUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    refreshed = await services.room_repository.get_room(room_slug)
    return ReplayResponse(room=refreshed or room, playback=playback)


@router.post("/{room_slug}/playback", response_model=ReplayResponse)
async def change_playback(
    room_slug: str, payload: PlaybackRequest, services: Services
) -> ReplayResponse:
    room = await require_room(room_slug, services)
    try:
        if payload.action == "pause":
            playback = await services.room_replay.pause(room)
        elif payload.action == "resume":
            playback = await services.room_replay.resume(room)
        elif payload.action == "set_speed":
            assert payload.playback_speed is not None
            playback = await services.room_replay.set_speed(room, payload.playback_speed)
        elif payload.action == "seek_to_lap":
            assert payload.lap_number is not None
            playback = await services.room_replay.seek_to_lap(room, payload.lap_number)
        else:
            assert payload.sequence is not None
            playback = await services.room_replay.seek_to_sequence(room, payload.sequence)
    except ReplayUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    refreshed = await services.room_repository.get_room(room_slug)
    return ReplayResponse(room=refreshed or room, playback=playback)


@router.get("/{room_slug}/diagnostics", response_model=RoomDiagnosticsResponse)
async def room_diagnostics(
    room_slug: str,
    services: Services,
) -> RoomDiagnosticsResponse:
    if services.settings.app_env == "production" and not services.settings.room_diagnostics_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    room = await require_room(room_slug, services)
    if room.session_key is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No normalized session is linked to this room",
        )
    session_key = room.session_key
    raw_count = await services.raw_event_repository.count(session_key)
    normalized_count = await services.normalized_event_repository.count(session_key)
    snapshot_count = await services.snapshot_repository.count(session_key)
    latest_sequence = await services.normalized_event_repository.max_sequence(session_key)
    latest_events = await services.normalized_event_repository.list_for_session(
        session_key,
        after_sequence=max(0, latest_sequence - 20),
        limit=20,
    )
    playback = await services.room_repository.get_playback(room.id)
    race_state = await services.race_state.get_state(session_key)
    live = services.openf1_live.status()
    return RoomDiagnosticsResponse(
        room_slug=room.slug,
        raw_event_count=raw_count,
        normalized_event_count=normalized_count,
        snapshot_count=snapshot_count,
        latest_event_sequence=latest_sequence,
        ordering_buffer_pending=services.ordering_buffer.pending(session_key),
        stream_state=room.status.value,
        provider_mode=("live" if room.mode == RoomMode.LIVE else "replay"),
        connection_state=str(live["connection_state"]),
        latest_events=[event.model_dump(mode="json") for event in latest_events],
        race_state=race_state.model_dump(mode="json"),
        playback=playback,
        discussion=services.room_discussion.metrics.model_dump(),
    )


@router.get("/{room_slug}/stream")
async def stream_race_room(
    room_slug: str,
    request: Request,
    services: Services,
    after_sequence: int = Query(default=0, ge=0),
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    room = await require_room(room_slug, services)
    recovered_sequence = after_sequence
    if last_event_id is not None and last_event_id.isdigit():
        recovered_sequence = max(recovered_sequence, int(last_event_id))
    return StreamingResponse(
        race_room_stream(request, services, room.id, recovered_sequence),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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
