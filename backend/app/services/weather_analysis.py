# SPDX-License-Identifier: AGPL-3.0-only
"""Bounded descriptive weather changes; no forecast, grip or rain-intensity model."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, FiniteFloat, ValidationError

from app.domain.strategy import FactReference, SessionHistory, WeatherObservation

WEATHER_FIELDS = (
    "air_temperature",
    "track_temperature",
    "humidity",
    "rainfall",
    "wind_speed",
    "wind_direction",
)


class WeatherAnalysis(BaseModel):
    availability: Literal["available", "partial", "stale", "unavailable"] = "unavailable"
    current: WeatherObservation | None = None
    age_seconds: FiniteFloat | None = Field(default=None, ge=0)
    comparison_seconds: FiniteFloat | None = Field(default=None, gt=0)
    rainfall_transition: Literal[
        "rain_detected", "rain_no_longer_detected", "rain_present", "rain_absent", "unknown"
    ] = "unknown"
    air_temperature_change_celsius: FiniteFloat | None = None
    track_temperature_change_celsius: FiniteFloat | None = None
    humidity_change_percentage_points: FiniteFloat | None = None
    wind_speed_change_metres_per_second: FiniteFloat | None = None
    wind_direction_change_degrees: FiniteFloat | None = None
    evidence: list[FactReference] = Field(default_factory=list, max_length=2)
    limitations: list[str] = Field(
        default_factory=lambda: [
            "rainfall_presence_not_intensity",
            "not_a_track_wetness_or_grip_measurement",
            "observed_change_not_forecast",
        ]
    )


def analyze_weather(
    history: SessionHistory, *, as_of_sequence: int, as_of_time: datetime
) -> WeatherAnalysis:
    """Use a consumed history prefix and replay-aware time, never current wall time.

    Five-minute freshness and a fifteen-minute comparison horizon are conservative
    display policies, not a forecast or claims about provider sampling cadence.
    A rewind must first reconstruct history: later corrections can replace rows.
    """
    unavailable = WeatherAnalysis()
    if history.sequence > as_of_sequence:
        unavailable.limitations.append("history_ahead_of_view")
        return unavailable
    if (
        isinstance(as_of_sequence, bool)
        or as_of_sequence < 0
        or as_of_time.tzinfo is None
        or as_of_time.utcoffset() is None
        or not isinstance(history.weather, list)
        or len(history.weather) > 60
    ):
        return unavailable
    by_time: dict[datetime, WeatherObservation] = {}
    identities: dict[object, tuple[int, datetime]] = {}
    try:
        for stored in history.weather:
            row = WeatherObservation.model_validate(stored.model_dump(warnings=False), strict=True)
            ref = row.evidence
            if ref.observed_at.tzinfo is None or ref.observed_at.utcoffset() is None:
                return unavailable
            identity = (ref.sequence, ref.observed_at)
            if ref.event_id in identities and identities[ref.event_id] != identity:
                return unavailable
            identities[ref.event_id] = identity
            if ref.sequence > min(as_of_sequence, history.sequence) or ref.observed_at > as_of_time:
                continue
            prior = by_time.get(ref.observed_at)
            if prior is None or prior.evidence.sequence < ref.sequence:
                by_time[ref.observed_at] = row
    except (AttributeError, TypeError, ValueError, OverflowError, ValidationError):
        return unavailable
    rows = sorted(by_time.values(), key=lambda row: row.evidence.observed_at)
    if not rows:
        return unavailable
    current = rows[-1]
    if not any(getattr(current, field) is not None for field in WEATHER_FIELDS):
        return unavailable
    age = (as_of_time - current.evidence.observed_at).total_seconds()
    if age > 300:
        return WeatherAnalysis(
            availability="stale", current=current, age_seconds=age, evidence=[current.evidence]
        )
    previous = rows[-2] if len(rows) > 1 else None
    elapsed = (
        (current.evidence.observed_at - previous.evidence.observed_at).total_seconds()
        if previous is not None
        else None
    )
    if elapsed is not None and elapsed > 900:
        previous, elapsed = None, None
    if previous is not None and not any(
        getattr(previous, field) is not None and getattr(current, field) is not None
        for field in WEATHER_FIELDS
    ):
        previous, elapsed = None, None
    rainfall = (
        "unknown"
        if current.rainfall is None
        else "rain_present"
        if current.rainfall
        else "rain_absent"
    )
    if previous is not None and previous.rainfall is not None and current.rainfall is not None:
        if previous.rainfall != current.rainfall:
            rainfall = "rain_detected" if current.rainfall else "rain_no_longer_detected"
    changes = {}
    for field, target in (
        ("air_temperature", "air_temperature_change_celsius"),
        ("track_temperature", "track_temperature_change_celsius"),
        ("humidity", "humidity_change_percentage_points"),
        ("wind_speed", "wind_speed_change_metres_per_second"),
        ("wind_direction", "wind_direction_change_degrees"),
    ):
        before = getattr(previous, field) if previous is not None else None
        after = getattr(current, field)
        if before is not None and after is not None:
            delta = after - before
            changes[target] = (delta + 180) % 360 - 180 if field == "wind_direction" else delta
    return WeatherAnalysis(
        availability="available" if previous is not None else "partial",
        current=current,
        age_seconds=age,
        comparison_seconds=elapsed,
        rainfall_transition=rainfall,
        evidence=([previous.evidence] if previous is not None else []) + [current.evidence],
        **changes,
    )
