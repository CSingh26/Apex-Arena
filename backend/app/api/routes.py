# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import hmac
import logging
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.room_schemas import (
    EventWeekendListResponse,
    SessionBootstrapResponse,
    SessionCapabilitiesResponse,
    SessionListResponse,
)
from app.api.schemas import (
    AppHealth,
    ChampionshipSummaryResponse,
    ComponentHealth,
    ConstructorStandingsResponse,
    DebugConfigResponse,
    DriverStandingsResponse,
    EngineStatusResponse,
    HealthResponse,
    HistoricalIngestionRequest,
    HistoricalIngestionResponse,
    LiveStatusResponse,
    OpenF1StatusResponse,
    RaceEventCategory,
    SeasonCalendarSummary,
    SessionEventsResponse,
    SessionIntelligenceResponse,
    SessionLocationSamplesResponse,
    SessionLocationsResponse,
    SessionStateResponse,
    SessionTelemetryResponse,
    SessionTimingResponse,
    SessionTrackResponse,
)
from app.api.streaming import session_event_stream
from app.domain.models import (
    EventImportance,
    EventOrigin,
    MeetingLifecycleStatus,
    RaceEventType,
)
from app.domain.rooms import SessionBootstrap
from app.providers.jolpica import JolpicaPayloadError
from app.services.championship import ChampionshipUnavailableError
from app.services.container import AppServices
from app.services.historical import HistoricalIngestionError
from app.services.openf1_backfill import backfill_job_status
from app.services.session_realtime import (
    SessionLocationSamplesState,
    SessionTrackState,
    location_state,
    location_state_from_samples,
    telemetry_state,
    timing_state,
)

logger = logging.getLogger(__name__)
router = APIRouter()

EVENT_CATEGORY_TYPES: dict[RaceEventCategory, set[RaceEventType]] = {
    RaceEventCategory.BATTLES: {
        RaceEventType.BATTLE_STARTED,
        RaceEventType.BATTLE_INTENSIFIED,
        RaceEventType.BATTLE_ENDED,
        RaceEventType.DRS_RANGE_ENTERED,
        RaceEventType.DRS_RANGE_EXITED,
        RaceEventType.OVERTAKE,
    },
    RaceEventCategory.PITS: {
        RaceEventType.PIT_STOP,
        RaceEventType.PIT_ENTRY,
        RaceEventType.PIT_EXIT,
        RaceEventType.TYRE_CHANGE,
    },
    RaceEventCategory.RACE_CONTROL: {
        RaceEventType.RACE_CONTROL,
        RaceEventType.SAFETY_CAR,
        RaceEventType.VIRTUAL_SAFETY_CAR,
        RaceEventType.RED_FLAG,
        RaceEventType.YELLOW_FLAG,
        RaceEventType.PENALTY,
        RaceEventType.INVESTIGATION,
    },
    RaceEventCategory.FAST_LAPS: {
        RaceEventType.FASTEST_LAP,
        RaceEventType.PERSONAL_BEST,
    },
}


def get_services(request: Request) -> AppServices:
    return request.app.state.services


Services = Annotated[AppServices, Depends(get_services)]


async def _session_geometry(services: AppServices, session_key: str):
    try:
        return await services.session_locations.geometry(session_key)
    except Exception as exc:
        logger.warning(
            "location_geometry_unavailable session_key=%s error=%s",
            session_key,
            type(exc).__name__,
        )
        return None


async def _location_sample_count(services: AppServices, room: object) -> int | None:
    """Real stored sample count, or None when it cannot be determined.

    None keeps the capability at ``unknown`` rather than falsely reporting the
    map as unavailable when the store itself is the thing that failed.
    """

    session_key = getattr(room, "session_key", None)
    if not session_key:
        return None
    try:
        return await services.session_locations.sample_count(str(session_key))
    except Exception as exc:
        logger.warning(
            "location_capability_lookup_failed session_key=%s error=%s",
            session_key,
            type(exc).__name__,
        )
        return None


def _utc(value: datetime | None) -> datetime | None:
    """Treat naive query timestamps as UTC; provider samples are always UTC."""

    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def _session_intelligence(
    services: AppServices,
    session_key: str | None,
) -> SessionIntelligenceResponse:
    race_state = getattr(services, "race_state", None)
    if not session_key or race_state is None:
        return SessionIntelligenceResponse(session_key=session_key or "")
    return SessionIntelligenceResponse.from_state(await race_state.get_state(session_key))


@router.get("/api/v1/season/{season}/weekends", response_model=EventWeekendListResponse)
async def season_weekends(season: int, services: Services) -> EventWeekendListResponse:
    events, total = await services.rooms.grouped_events(season=season, limit=100, offset=0)
    return EventWeekendListResponse(events=events, total=total, limit=100, offset=0)


@router.get("/api/v1/weekends/{event_slug}")
async def weekend_detail(event_slug: str, services: Services):
    event = await services.rooms.event_weekend(event_slug)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Weekend not found")
    return event


@router.get("/api/v1/weekends/{event_slug}/sessions", response_model=SessionListResponse)
async def weekend_sessions(event_slug: str, services: Services) -> SessionListResponse:
    event = await services.rooms.event_weekend(event_slug)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Weekend not found")
    return SessionListResponse(sessions=event.sessions)


@router.get(
    "/api/v1/sessions/{session_id}/capabilities", response_model=SessionCapabilitiesResponse
)
async def session_capabilities(session_id: UUID, services: Services) -> SessionCapabilitiesResponse:
    result = await services.rooms.session_bootstrap(session_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    _, _, room = result
    return SessionCapabilitiesResponse(
        **services.rooms.capabilities_for(
            room, location_samples=await _location_sample_count(services, room)
        ).model_dump()
    )


@router.get("/api/v1/sessions/{session_id}/room", response_model=SessionBootstrapResponse)
@router.get("/api/v1/sessions/{session_id}", response_model=SessionBootstrapResponse)
async def session_detail(session_id: UUID, services: Services) -> SessionBootstrapResponse:
    result = await services.rooms.session_bootstrap(session_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    weekend, session, room = result
    bootstrap = SessionBootstrap(
        session=session,
        weekend=weekend,
        room_status=session.eligibility,
        capabilities=services.rooms.capabilities_for(
            room, location_samples=await _location_sample_count(services, room)
        ),
        room_slug=session.room_slug,
    )
    return SessionBootstrapResponse(
        **bootstrap.model_dump(),
        intelligence=await _session_intelligence(
            services,
            str(getattr(room, "session_key", "") or "") or None,
        ),
    )


@router.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {"name": "Apex Arena API", "docs": "/docs", "health": "/health"}


@router.get("/health/live")
async def health_live(services: Services) -> dict[str, object]:
    """Cheap process liveness probe; it deliberately avoids network dependencies."""
    return {
        "status": "alive",
        "role": services.settings.app_process_role,
        "checked_at": datetime.now(UTC).isoformat(),
    }


@router.get("/health/ready", response_model=None)
async def health_ready(services: Services) -> JSONResponse:
    """Dependency-aware readiness probe suitable for traffic admission."""
    database_result, redis_result = await asyncio.gather(
        services.database.health_check(),
        services.redis.health_check(),
    )
    database_ok, _ = database_result
    redis_ok, _ = redis_result
    ready = database_ok and redis_ok
    return JSONResponse(
        status_code=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "status": "ready" if ready else "not_ready",
            "role": services.settings.app_process_role,
            "dependencies": {
                "database": "ready" if database_ok else "unavailable",
                "redis": "ready" if redis_ok else "unavailable",
            },
            "checked_at": datetime.now(UTC).isoformat(),
        },
    )


@router.get("/health/provider", response_model=None)
@router.get("/health/providers", response_model=None, include_in_schema=False)
async def health_provider(services: Services) -> JSONResponse:
    """Report the latest OpenF1 ingestion state without exposing credentials or tokens."""
    provider: dict[str, object] | None
    source = "local_process"
    if services.settings.app_process_role == "api":
        source = "redis_status_stream"
        try:
            provider = await services.event_bus.latest_connection_status()
        except Exception:
            provider = None
    else:
        provider = services.openf1_live.status()

    live_disabled = (
        not services.settings.live_mode_enabled
        or services.settings.openf1_ingestion_mode == "rest"
        or not services.settings.openf1_live_auto_connect
    )
    state = str((provider or {}).get("connection_state") or "unknown").upper()
    if live_disabled and state in {"UNKNOWN", "DISCONNECTED"}:
        state = "DISABLED"
    healthy = live_disabled or state in {"CONNECTED", "DISABLED"}
    return JSONResponse(
        status_code=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "status": "ready" if healthy else "degraded",
            "source": source,
            "connection_state": state,
            "current_session_key": (provider or {}).get("current_session_key"),
            "last_event_at": str((provider or {}).get("last_event_at") or "") or None,
            "reconciliation": services.recent_reconciliation.status,
            "checked_at": datetime.now(UTC).isoformat(),
        },
    )


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
            detail="AI configuration is available; automated reactions are not running",
        ),
    )


@router.get("/api/v1/openf1/status", response_model=OpenF1StatusResponse)
async def openf1_status(services: Services) -> OpenF1StatusResponse:
    rest_status = services.openf1.status
    return OpenF1StatusResponse(
        **rest_status,
        live_auth_ready=services.settings.openf1_credentials_present,
    )


@router.get("/api/v1/internal/openf1/backfill-status", response_model=None)
async def openf1_backfill_status(
    services: Services,
    internal_api_key: Annotated[str | None, Header(alias="X-Internal-API-Key")] = None,
) -> dict[str, object]:
    configured = services.settings.internal_api_key
    if (
        configured is None
        or internal_api_key is None
        or not hmac.compare_digest(internal_api_key, configured.get_secret_value())
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal key")
    latest = await services.backfill_jobs.latest()
    live = services.openf1_live.status()
    return {
        "process_role": services.settings.app_process_role,
        "ingestion_mode": services.settings.openf1_ingestion_mode,
        "mqtt_state": live["connection_state"],
        "rest_backfill_enabled": services.settings.openf1_rest_backfill_enabled,
        "recent_session_reconciliation": services.recent_reconciliation.status,
        "current_job": backfill_job_status(latest),
        "advisory_lease_owner": services.database.ingestor_lease_owned,
        "last_provider_event_timestamp": live["last_event_at"],
    }


@router.get("/api/v1/live/status", response_model=LiveStatusResponse)
async def live_status(services: Services) -> LiveStatusResponse:
    return LiveStatusResponse(**services.openf1_live.status())


@router.get(
    "/api/v1/championship/drivers",
    response_model=DriverStandingsResponse,
)
async def championship_drivers(services: Services) -> DriverStandingsResponse:
    try:
        return await services.championship.drivers()
    except ChampionshipUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


@router.get(
    "/api/v1/championship/constructors",
    response_model=ConstructorStandingsResponse,
)
async def championship_constructors(services: Services) -> ConstructorStandingsResponse:
    try:
        return await services.championship.constructors()
    except ChampionshipUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


@router.get(
    "/api/v1/championship/summary",
    response_model=ChampionshipSummaryResponse,
)
async def championship_summary(services: Services) -> ChampionshipSummaryResponse:
    try:
        return await services.championship.summary()
    except ChampionshipUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


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
        redis=ComponentHealth(status="healthy" if redis_ok else "degraded", detail=redis_detail),
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
    before_sequence_number: int | None = Query(default=None, ge=0),
    limit: int = Query(default=100, ge=1, le=250),
    event_type: Annotated[list[RaceEventType] | None, Query()] = None,
    category: RaceEventCategory | None = None,
    driver_number: int | None = Query(default=None, ge=1, le=999),
    lap_number: int | None = Query(default=None, ge=0),
    minimum_importance: EventImportance | None = None,
    event_origin: EventOrigin | None = None,
    before_time: datetime | None = None,
) -> SessionEventsResponse:
    event_types = event_type
    if category is not None:
        category_types = EVENT_CATEGORY_TYPES[category]
        event_types = (
            [value for value in event_type if value in category_types]
            if event_type
            else sorted(category_types, key=lambda value: value.value)
        )
    events = await services.normalized_event_repository.list_for_session(
        session_key,
        after_sequence=after_sequence_number,
        before_sequence=before_sequence_number,
        limit=limit,
        event_types=event_types,
        driver_number=driver_number,
        lap_number=lap_number,
        minimum_importance=minimum_importance,
        event_origin=event_origin,
        before_time=_utc(before_time),
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


@router.get(
    "/api/v1/sessions/{session_key}/timing",
    response_model=SessionTimingResponse,
)
async def session_timing(session_key: str, services: Services) -> SessionTimingResponse:
    return SessionTimingResponse(
        timing=timing_state(await services.race_state.get_state(session_key))
    )


@router.get(
    "/api/v1/sessions/{session_key}/drivers/{driver_number}/telemetry",
    response_model=SessionTelemetryResponse,
)
async def session_telemetry(
    session_key: str,
    driver_number: int,
    services: Services,
) -> SessionTelemetryResponse:
    return SessionTelemetryResponse(
        telemetry=telemetry_state(await services.race_state.get_state(session_key), driver_number)
    )


@router.get(
    "/api/v1/sessions/{session_key}/locations",
    response_model=SessionLocationsResponse,
)
async def session_locations(
    session_key: str,
    services: Services,
    at: Annotated[
        datetime | None,
        Query(description="Replay clock in UTC; returns the latest fix at or before it"),
    ] = None,
) -> SessionLocationsResponse:
    """Latest known track position per driver.

    Live sessions read the reduced race state; a replay clock (``at``) or an
    empty live state falls back to the persisted series, so historical and
    live sessions return the same contract.
    """

    state = await services.race_state.get_state(session_key)
    locations = location_state(state)
    if at is not None or not locations.drivers:
        try:
            samples = await services.session_locations.latest(session_key, at=_utc(at))
        except Exception as exc:
            logger.error(
                "location_lookup_failed session_key=%s error=%s",
                session_key,
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Driver track positions are temporarily unavailable",
            ) from exc
        if samples:
            locations = location_state_from_samples(state, samples)
    # Bounds are an optimisation: without them the map derives its own extent
    # from the fixes it has, so a geometry gap must not fail the request.
    geometry = await _session_geometry(services, session_key)
    if geometry is not None:
        locations.bounds = geometry.bounds
    logger.debug(
        "location_lookup session_key=%s source=%s drivers=%s at=%s",
        session_key,
        locations.source,
        len(locations.drivers),
        at.isoformat() if at else None,
    )
    return SessionLocationsResponse(locations=locations)


@router.get(
    "/api/v1/sessions/{session_key}/locations/samples",
    response_model=SessionLocationSamplesResponse,
)
async def session_location_samples(
    session_key: str,
    services: Services,
    since: datetime | None = None,
    until: datetime | None = None,
    driver_number: Annotated[int | None, Query(ge=1, le=199)] = None,
    limit: Annotated[int, Query(ge=1, le=20_000)] = 6_000,
) -> SessionLocationSamplesResponse:
    """Windowed provider fixes so the map can interpolate between samples."""

    samples = await services.session_locations.window(
        session_key,
        since=_utc(since),
        until=_utc(until),
        driver_number=driver_number,
        limit=limit,
    )
    return SessionLocationSamplesResponse(
        locations=SessionLocationSamplesState(
            session_key=session_key,
            count=len(samples),
            drivers=sorted({sample.driver_number for sample in samples}),
            since=since,
            until=until,
            samples=samples,
        )
    )


@router.get(
    "/api/v1/sessions/{session_key}/track",
    response_model=SessionTrackResponse,
)
async def session_track(session_key: str, services: Services) -> SessionTrackResponse:
    """Circuit outline traced from this session's own location samples."""

    geometry = await _session_geometry(services, session_key)
    first_sample_at, last_sample_at = await services.session_locations.time_range(session_key)
    if geometry is None:
        return SessionTrackResponse(track=SessionTrackState(session_key=session_key))
    return SessionTrackResponse(
        track=SessionTrackState(
            session_key=session_key,
            available=bool(geometry.path),
            bounds=geometry.bounds,
            path=geometry.path,
            source_driver_number=geometry.source_driver_number,
            sample_count=geometry.sample_count,
            first_sample_at=first_sample_at,
            last_sample_at=last_sample_at,
        )
    )


@router.get("/api/v1/stream/sessions/{session_key}")
async def stream_session(
    session_key: str,
    request: Request,
    services: Services,
    last_sequence_number: int = Query(default=0, ge=0),
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    recovered_sequence = last_sequence_number
    if last_event_id is not None and last_event_id.isdigit():
        recovered_sequence = max(recovered_sequence, int(last_event_id))
    return StreamingResponse(
        session_event_stream(request, services, session_key, recovered_sequence),
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
    except (httpx.HTTPError, HistoricalIngestionError) as exc:
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
    if settings.app_env == "production" and not settings.room_diagnostics_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
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
