# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.domain.models import RaceMeeting


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
    live_auth_ready: bool
    supported_endpoints: list[str]


class LiveStatusResponse(BaseModel):
    live_mode_enabled: bool
    credentials_present: bool
    token_available: bool
    token_expires_in_seconds: int | None
    connection_state: str


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
