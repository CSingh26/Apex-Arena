# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import hmac
import logging
from datetime import UTC, datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.api.schemas import (
    AppHealth,
    ComponentHealth,
    DebugConfigResponse,
    EngineStatusResponse,
    HealthResponse,
    HistoricalIngestionRequest,
    HistoricalIngestionResponse,
    LiveStatusResponse,
    OpenF1StatusResponse,
    SeasonCalendarSummary,
    SessionEventsResponse,
    SessionStateResponse,
)
from app.api.streaming import session_event_stream
from app.domain.models import MeetingLifecycleStatus
from app.providers.jolpica import JolpicaPayloadError
from app.services.container import AppServices

logger = logging.getLogger(__name__)
router = APIRouter()


def get_services(request: Request) -> AppServices:
    return request.app.state.services


Services = Annotated[AppServices, Depends(get_services)]


@router.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {"name": "Apex Arena API", "docs": "/docs", "health": "/health"}


@router.get("/health", response_model=HealthResponse)
async def health(services: Services) -> HealthResponse:
    settings = services.settings
    database_result, redis_result = await asyncio.gather(
        services.database.health_check(),
        services.redis.health_check(),
    )
    database_ok, database_detail = database_result
    redis_ok, redis_detail = redis_result

    if not settings.live_mode_enabled:
        live_status = "disabled"
        live_detail = "Live mode is disabled"
    elif settings.openf1_credentials_present:
        live_status = "ready"
        live_detail = "Credentials present; TLS MQTT client is available"
    else:
        live_status = "degraded"
        live_detail = "Credentials missing; historical REST remains available"

    overall_healthy = database_ok and redis_ok and live_status != "degraded"
    return HealthResponse(
        status="healthy" if overall_healthy else "degraded",
        checked_at=datetime.now(UTC),
        app=AppHealth(
            status="healthy",
            name=settings.app_name,
            environment=settings.app_env,
            season=settings.season_year,
        ),
        database=ComponentHealth(
            status="healthy" if database_ok else "degraded", detail=database_detail
        ),
        redis=ComponentHealth(status="healthy" if redis_ok else "degraded", detail=redis_detail),
        openf1_rest=ComponentHealth(
            status="configured",
            detail="Historical REST is configured with an OAuth retry when required",
        ),
        openf1_live=ComponentHealth(status=live_status, detail=live_detail),
        jolpica=ComponentHealth(status="configured", detail="2026 calendar provider configured"),
        ai=ComponentHealth(
            status="enabled" if settings.ai_enabled and not settings.ai_kill_switch else "disabled",
            detail="Day 2 exposes configuration only; AI reactions are not running",
        ),
    )


@router.get("/api/v1/openf1/status", response_model=OpenF1StatusResponse)
async def openf1_status(services: Services) -> OpenF1StatusResponse:
    rest_status = services.openf1.status
    return OpenF1StatusResponse(
        **rest_status,
        live_auth_ready=services.settings.openf1_credentials_present,
    )


@router.get("/api/v1/live/status", response_model=LiveStatusResponse)
async def live_status(services: Services) -> LiveStatusResponse:
    return LiveStatusResponse(**services.openf1_live.status())


@router.get("/api/v1/engine/status", response_model=EngineStatusResponse)
async def engine_status(services: Services) -> EngineStatusResponse:
    current_session_key = (
        services.openf1_live.current_session_key
        or await services.normalized_event_repository.latest_session_key()
    )
    (
        database_result,
        redis_result,
        raw_count,
        normalized_count,
        snapshot_count,
        latest_ingestion,
    ) = await asyncio.gather(
        services.database.health_check(),
        services.redis.health_check(),
        services.raw_event_repository.count(current_session_key),
        services.normalized_event_repository.count(current_session_key),
        services.snapshot_repository.count(current_session_key),
        services.ingestion_runs.latest(),
    )
    latest_sequence = (
        await services.normalized_event_repository.max_sequence(current_session_key)
        if current_session_key
        else 0
    )
    database_ok, database_detail = database_result
    redis_ok, redis_detail = redis_result
    live = LiveStatusResponse(**services.openf1_live.status())
    return EngineStatusResponse(
        status="ready" if database_ok and redis_ok else "degraded",
        generated_at=datetime.now(UTC),
        database=ComponentHealth(
            status="healthy" if database_ok else "degraded", detail=database_detail
        ),
        redis=ComponentHealth(
            status="healthy" if redis_ok else "degraded", detail=redis_detail
        ),
        current_session_key=current_session_key,
        raw_event_count=raw_count,
        normalized_event_count=normalized_count,
        snapshot_count=snapshot_count,
        latest_sequence_number=latest_sequence,
        ordering_buffer_pending=services.ordering_buffer.pending(current_session_key),
        historical_ingestion_enabled=services.settings.historical_ingestion_enabled,
        debug_ingestion_enabled=services.settings.debug_ingestion_enabled,
        live=live,
        latest_ingestion=latest_ingestion,
    )


@router.get(
    "/api/v1/sessions/{session_key}/events",
    response_model=SessionEventsResponse,
)
async def session_events(
    session_key: str,
    services: Services,
    after_sequence_number: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
) -> SessionEventsResponse:
    events = await services.normalized_event_repository.list_for_session(
        session_key,
        after_sequence=after_sequence_number,
        limit=limit,
    )
    return SessionEventsResponse(
        session_key=session_key,
        after_sequence_number=after_sequence_number,
        count=len(events),
        events=events,
    )


@router.get(
    "/api/v1/sessions/{session_key}/state",
    response_model=SessionStateResponse,
)
async def session_state(session_key: str, services: Services) -> SessionStateResponse:
    return SessionStateResponse(state=await services.race_state.get_state(session_key))


@router.get("/api/v1/stream/sessions/{session_key}")
async def stream_session(
    session_key: str,
    request: Request,
    services: Services,
    last_sequence_number: int = Query(default=0, ge=0),
) -> StreamingResponse:
    return StreamingResponse(
        session_event_stream(request, services, session_key, last_sequence_number),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/api/v1/debug/ingest-historical-session",
    response_model=HistoricalIngestionResponse,
)
async def ingest_historical_session(
    payload: HistoricalIngestionRequest,
    services: Services,
    internal_api_key: Annotated[str | None, Header(alias="X-Internal-API-Key")] = None,
) -> HistoricalIngestionResponse:
    settings = services.settings
    if not settings.debug_ingestion_enabled or not settings.historical_ingestion_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ingestion is disabled")
    configured_key = settings.internal_api_key
    if configured_key is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal ingestion is not configured",
        )
    if internal_api_key is None or not hmac.compare_digest(
        internal_api_key,
        configured_key.get_secret_value(),
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal key")
    try:
        result = await services.historical.ingest_session(payload.session_key, payload.endpoints)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except httpx.HTTPError as exc:
        logger.warning("Historical OpenF1 provider unavailable: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The historical provider is temporarily unavailable",
        ) from exc
    return HistoricalIngestionResponse.model_validate(result.model_dump())


@router.get("/api/v1/season/{year}", response_model=SeasonCalendarSummary)
async def season_calendar(year: int, services: Services) -> SeasonCalendarSummary:
    settings = services.settings
    if settings.season_only_mode and year != settings.season_year:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Apex Arena v0.1 supports only the {settings.season_year} season",
        )

    try:
        races = await services.season.calendar(year)
    except (httpx.HTTPError, JolpicaPayloadError, KeyError, TypeError, ValueError) as exc:
        logger.warning("Jolpica calendar unavailable: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The season calendar provider is temporarily unavailable",
        ) from exc

    completed = sum(race.status == MeetingLifecycleStatus.COMPLETED for race in races)
    upcoming = sum(race.status == MeetingLifecycleStatus.UPCOMING for race in races)
    live = sum(race.status == MeetingLifecycleStatus.LIVE for race in races)
    return SeasonCalendarSummary(
        season_year=year,
        generated_at=datetime.now(UTC),
        total_races=len(races),
        completed_races=completed,
        upcoming_races=upcoming,
        live_races=live,
        target_found=any(race.is_target for race in races),
        target_grand_prix=settings.target_grand_prix,
        target_circuit=settings.target_circuit,
        races=races,
    )


@router.get("/api/v1/debug/config", response_model=DebugConfigResponse)
async def debug_config(services: Services) -> DebugConfigResponse:
    settings = services.settings
    return DebugConfigResponse(
        runtime=settings.safe_runtime_metadata,
        features={
            "live_rooms": settings.enable_live_rooms,
            "historical_replay": settings.enable_historical_replay,
            "auto_room_creation": settings.enable_auto_room_creation,
            "public_replays": settings.enable_public_replays,
            "user_chat": settings.enable_user_chat,
            "user_created_agents": settings.enable_user_created_agents,
            "vector_memory": settings.enable_vector_memory,
            "monte_carlo": settings.enable_monte_carlo,
        },
    )
