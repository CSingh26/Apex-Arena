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


class ChampionshipMetadata(BaseModel):
    season: int
    generated_at: datetime
    latest_completed_event: str | None = None
    races_completed: int
    races_remaining: int | None = None
    source: str
    cached: bool = False
    cache_age_seconds: int = 0
    live: bool = False
    provisional: bool = False
    stale: bool = False


class ChampionshipDriverRef(BaseModel):
    driver_id: str
    driver_number: int | None = None
    full_name: str
    acronym: str | None = None
    headshot_url: str | None = None
    points: float | None = None


class DriverStanding(BaseModel):
    position: int
    driver_id: str
    driver_number: int | None = None
    first_name: str | None = None
    last_name: str | None = None
    full_name: str
    acronym: str | None = None
    country_code: str | None = None
    headshot_url: str | None = None
    team_id: str | None = None
    team_name: str | None = None
    team_colour: str | None = None
    points: float
    wins: int | None = None
    podiums: int | None = None
    top_5_finishes: int | None = None
    top_10_finishes: int | None = None
    poles: int | None = None
    fastest_laps: int | None = None
    race_starts: int | None = None
    classified_finishes: int | None = None
    dnfs: int | None = None
    dsqs: int | None = None
    sprint_starts: int | None = None
    sprint_wins: int | None = None
    sprint_podiums: int | None = None
    sprint_points: float | None = None
    best_sprint_finish: int | None = None
    average_finish: float | None = None
    best_finish: int | None = None
    worst_classified_finish: int | None = None
    average_grid_position: float | None = None
    average_qualifying_position: float | None = None
    best_qualifying_result: int | None = None
    q3_appearances: int | None = None
    positions_gained_lost: int | None = None
    championship_position_change: int | None = None
    points_change_from_previous_race: float | None = None
    latest_race_finish: int | None = None
    latest_race_points: float | None = None
    races_completed: int
    points_per_race: float | None = None
    podium_percentage: float | None = None
    points_finishing_percentage: float | None = None


class ConstructorStanding(BaseModel):
    position: int
    constructor_id: str
    team_name: str
    team_colour: str | None = None
    logo_url: str | None = None
    points: float
    wins: int | None = None
    podiums: int | None = None
    poles: int | None = None
    fastest_laps: int | None = None
    race_starts: int | None = None
    double_podiums: int | None = None
    dnfs: int | None = None
    sprint_wins: int | None = None
    sprint_podiums: int | None = None
    average_finish: float | None = None
    average_points_per_event: float | None = None
    championship_position_change: int | None = None
    points_change_from_previous_race: float | None = None
    drivers: list[ChampionshipDriverRef] = Field(default_factory=list)
    races_completed: int


class DriverStandingsResponse(BaseModel):
    standings: list[DriverStanding]
    metadata: ChampionshipMetadata


class ConstructorStandingsResponse(BaseModel):
    standings: list[ConstructorStanding]
    metadata: ChampionshipMetadata


class ChampionshipLeader(BaseModel):
    id: str
    name: str
    points: float
    advantage: float | None = None
    headshot_url: str | None = None
    team_colour: str | None = None


class ChampionshipSummaryResponse(BaseModel):
    driver_leader: ChampionshipLeader | None = None
    constructor_leader: ChampionshipLeader | None = None
    closest_title_battle: dict[str, Any] | None = None
    races_completed: int
    races_remaining: int | None = None
    latest_race: str | None = None
    next_race: str | None = None
    metadata: ChampionshipMetadata
