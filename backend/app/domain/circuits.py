# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CircuitRecord(BaseModel):
    label: str
    value: str
    detail: str | None = None


class CircuitIntelligence(BaseModel):
    circuit_name: str
    records: list[CircuitRecord] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    source_url: str | None = None


class SessionWeather(BaseModel):
    available: bool = False
    source_checked_at: datetime | None = None
    source_age_seconds: float = Field(default=0, ge=0)
    source_stale: bool = False
    sampled_at: datetime | None = None
    air_temperature_c: float | None = None
    track_temperature_c: float | None = None
    rainfall: bool | None = None
    humidity_percent: float | None = None
    pressure_mbar: float | None = None
    wind_speed_mps: float | None = None
    wind_direction_degrees: float | None = None
    source: str = "OpenF1"
    notice: str
