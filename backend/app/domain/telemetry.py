# SPDX-License-Identifier: AGPL-3.0-only
"""Bounded historical car telemetry for comparison views.

Telemetry is only ever as complete as the provider actually published. These
models keep the sample series, the channels genuinely present in it and the
reason any channel is missing together, so a chart can say "no brake trace for
this driver" rather than drawing a flat line at zero.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

SessionKey = Annotated[str, Field(min_length=1, max_length=128)]

# Query ceilings. Telemetry arrives at several hertz per car, so an unbounded
# window is a denial-of-service on our own database as much as a slow chart.
MAX_COMPARISON_DRIVERS = 2
MAX_SAMPLES_PER_DRIVER = 600
MAX_SCANNED_EVENTS = 4_000

# Channel names as published to clients, with the units they are measured in.
CHANNEL_UNITS: dict[str, str] = {
    "speed": "km/h",
    "throttle": "%",
    "brake": "%",
    "rpm": "rpm",
    "gear": "",
    "drs": "",
}

# Plausible ranges for each numeric channel, shared by the live reduction and
# the history reader so a value one path rejects cannot survive in the other.
# A reading outside these, or a non-finite one, is provider corruption: it is
# dropped rather than charted.
CHANNEL_BOUNDS: dict[str, tuple[float, float]] = {
    "speed": (0, 450),
    "throttle": (0, 100),
    "brake": (0, 100),
    "rpm": (0, 20_000),
    "gear": (-1, 8),
}


class TelemetrySample(BaseModel):
    """One car-data observation. Absent channels stay absent, never zero."""

    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=0)
    observed_at: AwareDatetime
    lap_number: int | None = Field(default=None, ge=0)
    speed: float | None = None
    throttle: float | None = None
    brake: float | None = None
    rpm: int | None = None
    gear: int | None = None
    drs: bool | None = None


class DriverTelemetry(BaseModel):
    """One driver's bounded series plus what it can actually support."""

    model_config = ConfigDict(extra="forbid")

    driver_number: int = Field(ge=1)
    samples: list[TelemetrySample] = Field(default_factory=list, max_length=MAX_SAMPLES_PER_DRIVER)
    # Only channels with at least one observed value. A chart must not offer an
    # axis the provider never published for this driver.
    channels: list[str] = Field(default_factory=list, max_length=len(CHANNEL_UNITS))
    samples_truncated: bool = False


class TelemetryWindow(BaseModel):
    """A telemetry read, including why it may be empty."""

    model_config = ConfigDict(extra="forbid")

    session_key: SessionKey
    availability: Literal["available", "partial", "unavailable"] = "unavailable"
    # Populated whenever availability is not "available", so the UI can explain
    # itself instead of rendering a blank card.
    reason: str | None = None
    lap_number: int | None = Field(default=None, ge=0)
    drivers: list[DriverTelemetry] = Field(default_factory=list, max_length=MAX_COMPARISON_DRIVERS)
    units: dict[str, str] = Field(default_factory=lambda: dict(CHANNEL_UNITS))
    # The consumed cursor this window was read at, so a caller can tell whether
    # it still matches the view it is rendering against.
    view_sequence: int = Field(default=0, ge=0)
    scan_limited: bool = False
