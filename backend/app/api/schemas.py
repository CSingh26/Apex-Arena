# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.domain.models import NormalizedRaceEvent, RaceMeeting
from app.services.historical import HistoricalIngestionResult, IngestionRunSummary
from app.services.race_state import RaceState


class ComponentHealth(BaseModel):
    status: str
    detail: str | None = None


class AppHealth(ComponentHealth):
    name: str
    environment: str
    season: int


class HealthResponse(BaseModel):
    status: str
    checked_at: datetime
    app: AppHealth
    database: ComponentHealth
    redis: ComponentHealth
    openf1_rest: ComponentHealth
    openf1_live: ComponentHealth
    jolpica: ComponentHealth
    ai: ComponentHealth


class OpenF1StatusResponse(BaseModel):
    rest_configured: bool
    rest_host: str | None
    historical_auth_required: bool = False
    historical_auth_mode: str = "public_only"
    live_auth_ready: bool
    supported_endpoints: list[str]


class LiveStatusResponse(BaseModel):
    live_mode_enabled: bool
    credentials_present: bool
    auth_available: bool
    token_available: bool
    token_expires_in_seconds: int | None
    connection_state: str
    last_event_at: datetime | None
    reconnect_attempts: int
    current_session_key: str | None
    degraded_reason: str | None


class EngineStatusResponse(BaseModel):
    status: str
    generated_at: datetime
    database: ComponentHealth
    redis: ComponentHealth
    current_session_key: str | None
    raw_event_count: int
    normalized_event_count: int
    snapshot_count: int
    latest_sequence_number: int
    ordering_buffer_pending: int
    historical_ingestion_enabled: bool
    debug_ingestion_enabled: bool
    live: LiveStatusResponse
    latest_ingestion: IngestionRunSummary | None


class SessionEventsResponse(BaseModel):
    session_key: str
    after_sequence_number: int
    count: int
    events: list[NormalizedRaceEvent]


class SessionStateResponse(BaseModel):
    state: RaceState


class HistoricalIngestionRequest(BaseModel):
    session_key: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._:-]+$")
    endpoints: list[str] | None = None


class HistoricalIngestionResponse(HistoricalIngestionResult):
    pass


class SeasonCalendarSummary(BaseModel):
    season_year: int
    source: str = "Jolpica"
    generated_at: datetime
    total_races: int
    completed_races: int
    upcoming_races: int
    live_races: int
    target_found: bool
    target_grand_prix: str
    target_circuit: str
    races: list[RaceMeeting] = Field(default_factory=list)


class DebugConfigResponse(BaseModel):
    runtime: dict[str, Any]
    features: dict[str, bool]
