# SPDX-License-Identifier: AGPL-3.0-only
"""Provider-neutral, bounded views for the live Race Room."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.locations import DriverLocationSample, TrackBounds, is_transmitting
from app.services.race_state import DriverRaceState, RaceState
from app.services.session_semantics import is_qualifying_session

TimingMode = Literal["race", "qualifying", "practice"]
TyreCompound = Literal["SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET", "UNKNOWN"]
LocationSource = Literal["live", "historical", "unavailable"]


class DriverTimingState(BaseModel):
    driver_number: int
    name: str
    abbreviation: str
    team_name: str | None = None
    position: int | None = None
    position_change: int | None = None
    gap_to_leader: float | str | None = None
    interval: float | str | None = None
    latest_lap: float | None = None
    best_lap: float | None = None
    tyre_compound: TyreCompound = "UNKNOWN"
    tyre_age_laps: int | None = None
    pit_stop_count: int = 0
    is_fastest: bool = False
    is_personal_best: bool = False
    status: str = "RUNNING"


class SessionTimingState(BaseModel):
    session_key: str
    sequence_number: int
    updated_at: datetime | None = None
    mode: TimingMode
    session_phase: str | None = None
    current_lap: int | None = None
    track_status: str = "UNKNOWN"
    drivers: list[DriverTimingState] = Field(default_factory=list)


class DriverTelemetryState(BaseModel):
    session_key: str
    driver_number: int
    sampled_at: datetime | None = None
    speed_kph: float | None = None
    throttle_percent: float | None = None
    brake_percent: float | None = None
    gear: int | None = None
    rpm: int | None = None
    drs_active: bool | None = None
    available: bool = False


class DriverLocationState(BaseModel):
    driver_number: int
    x: float
    y: float
    z: float | None = None
    sampled_at: datetime | None = None
    position: int | None = None
    abbreviation: str


class SessionLocationState(BaseModel):
    session_key: str
    sequence_number: int
    updated_at: datetime | None = None
    available: bool = False
    source: LocationSource = "unavailable"
    bounds: TrackBounds | None = None
    drivers: list[DriverLocationState] = Field(default_factory=list)


class SessionLocationSamplesState(BaseModel):
    """Windowed series used by the map to interpolate between provider fixes."""

    session_key: str
    count: int = 0
    drivers: list[int] = Field(default_factory=list)
    since: datetime | None = None
    until: datetime | None = None
    samples: list[DriverLocationSample] = Field(default_factory=list)


class SessionTrackState(BaseModel):
    """Circuit outline and viewport extent, in raw provider coordinates."""

    session_key: str
    available: bool = False
    bounds: TrackBounds | None = None
    path: list[tuple[float, float]] = Field(default_factory=list)
    source_driver_number: int | None = None
    sample_count: int = 0
    first_sample_at: datetime | None = None
    last_sample_at: datetime | None = None


def timing_state(state: RaceState) -> SessionTimingState:
    qualifying = is_qualifying_session(state.session_type)
    mode: TimingMode = (
        "qualifying"
        if qualifying
        else "race"
        if state.session_type
        in {
            "RACE",
            "SPRINT",
        }
        else "practice"
    )
    rows = [_timing_row(number, driver, state) for number, driver in state.drivers.items()]
    if qualifying:
        rows.sort(
            key=lambda row: (row.position is None, row.position or 10_000, row.best_lap or 10_000)
        )
    else:
        rows.sort(key=lambda row: (row.position is None, row.position or 10_000, row.driver_number))
    fastest = min((row.best_lap for row in rows if row.best_lap is not None), default=None)
    for row in rows:
        row.is_fastest = fastest is not None and row.best_lap == fastest
        row.is_personal_best = row.latest_lap is not None and row.latest_lap == row.best_lap
    return SessionTimingState(
        session_key=state.session_key,
        sequence_number=state.sequence_number,
        updated_at=state.last_updated_at,
        mode=mode,
        session_phase=state.current_phase,
        current_lap=state.current_lap,
        track_status=_track_status(state),
        drivers=rows,
    )


def telemetry_state(state: RaceState, driver_number: int) -> DriverTelemetryState:
    driver = state.drivers.get(str(driver_number))
    telemetry = driver.telemetry if driver else {}
    return DriverTelemetryState(
        session_key=state.session_key,
        driver_number=driver_number,
        sampled_at=driver.telemetry_updated_at if driver else None,
        speed_kph=_float(telemetry.get("speed")),
        throttle_percent=_float(telemetry.get("throttle")),
        brake_percent=_float(telemetry.get("brake")),
        gear=_integer(telemetry.get("gear")),
        rpm=_integer(telemetry.get("rpm")),
        drs_active=telemetry.get("drs") if isinstance(telemetry.get("drs"), bool) else None,
        available=bool(telemetry),
    )


def location_state(state: RaceState) -> SessionLocationState:
    """Latest live fix per driver, straight from the reduced race state."""

    rows: list[DriverLocationState] = []
    for number, driver in state.drivers.items():
        location = driver.location
        if "x" not in location or "y" not in location:
            continue
        driver_number = _integer(number) or driver.driver_number
        if driver_number is None:
            continue
        if not is_transmitting(location["x"], location["y"], location.get("z")):
            continue
        rows.append(
            DriverLocationState(
                driver_number=driver_number,
                x=location["x"],
                y=location["y"],
                z=location.get("z"),
                sampled_at=driver.location_updated_at,
                position=driver.position,
                abbreviation=_abbreviation(driver, driver_number),
            )
        )
    return _sorted_location_state(state, rows, source="live")


def location_state_from_samples(
    state: RaceState,
    samples: list[DriverLocationSample],
) -> SessionLocationState:
    """Persisted fixes joined to whatever driver metadata the state knows.

    A missing timing row must never drop a marker: the driver number alone is
    enough to place and label a car.
    """

    rows = [
        DriverLocationState(
            driver_number=sample.driver_number,
            x=sample.x,
            y=sample.y,
            z=sample.z,
            sampled_at=sample.sample_time,
            position=_driver_for(state, sample.driver_number).position,
            abbreviation=_abbreviation(
                _driver_for(state, sample.driver_number), sample.driver_number
            ),
        )
        for sample in samples
    ]
    return _sorted_location_state(state, rows, source="historical")


def _sorted_location_state(
    state: RaceState,
    rows: list[DriverLocationState],
    *,
    source: LocationSource,
) -> SessionLocationState:
    rows.sort(key=lambda row: (row.position is None, row.position or 10_000, row.driver_number))
    return SessionLocationState(
        session_key=state.session_key,
        sequence_number=state.sequence_number,
        updated_at=state.last_updated_at,
        available=bool(rows),
        source=source if rows else "unavailable",
        drivers=rows,
    )


def _driver_for(state: RaceState, driver_number: int) -> DriverRaceState:
    return state.drivers.get(str(driver_number)) or DriverRaceState(driver_number=driver_number)


def _timing_row(number: str, driver: DriverRaceState, state: RaceState) -> DriverTimingState:
    driver_number = _integer(number) or driver.driver_number or 0
    compound = _compound(driver.stint.get("compound") or driver.stint.get("tyre_compound"))
    stint_start = _integer(driver.stint.get("lap_start") or driver.stint.get("start_lap"))
    tyre_age = (
        state.current_lap - stint_start + 1
        if stint_start is not None
        and state.current_lap is not None
        and state.current_lap >= stint_start
        else None
    )
    return DriverTimingState(
        driver_number=driver_number,
        name=driver.full_name or driver.broadcast_name or f"Driver {driver_number}",
        abbreviation=_abbreviation(driver, driver_number),
        team_name=driver.team_name,
        position=driver.position or driver.final_position,
        position_change=driver.position_change,
        gap_to_leader=driver.gap_to_leader,
        interval=driver.interval,
        latest_lap=driver.latest_lap_duration,
        best_lap=driver.best_lap_duration,
        tyre_compound=compound,
        tyre_age_laps=tyre_age,
        pit_stop_count=len(driver.pit_stops),
        status="FINISHED" if state.status == "finished" else "RUNNING",
    )


def _abbreviation(driver: DriverRaceState, driver_number: int) -> str:
    name = driver.broadcast_name or driver.full_name
    if not name:
        return str(driver_number)
    parts = name.split()
    return parts[-1][:3].upper() if parts else str(driver_number)


def _compound(value: object) -> TyreCompound:
    text = str(value or "").upper()
    aliases = {
        "SOFT": "SOFT",
        "MEDIUM": "MEDIUM",
        "HARD": "HARD",
        "INTERMEDIATE": "INTERMEDIATE",
        "WET": "WET",
    }
    return aliases.get(text, "UNKNOWN")  # type: ignore[return-value]


def _track_status(state: RaceState) -> str:
    event_type = str(state.race_control_state.get("event_type") or "")
    return {
        "RED_FLAG": "RED FLAG",
        "SAFETY_CAR": "SAFETY CAR",
        "VIRTUAL_SAFETY_CAR": "VIRTUAL SAFETY CAR",
        "YELLOW_FLAG": "YELLOW",
    }.get(event_type, "GREEN" if state.status in {"started", "running"} else state.status.upper())


def _integer(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
