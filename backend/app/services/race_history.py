# SPDX-License-Identifier: AGPL-3.0-only
"""Pure, stored-order projections; source facts remain durable outside this window."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from app.domain.models import EventOrigin, NormalizedRaceEvent, RaceEventType
from app.domain.strategy import (
    DriverHistory,
    FactReference,
    LapObservation,
    PitObservation,
    SessionHistory,
    StintObservation,
    WeatherObservation,
)


def finite_number(value: object, minimum: float, maximum: float) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and minimum <= number <= maximum else None


def _integer(value: object, minimum: int = 1) -> int | None:
    number = finite_number(value, minimum, 100_000)
    return int(number) if number is not None and number.is_integer() else None


def _note_unresolved_deletion(state: SessionHistory) -> None:
    state.unresolved_deletions = min(state.unresolved_deletions + 1, 100_000)


def tyre_age_at_lap(stint: StintObservation, completed_lap: int) -> int | None:
    """Age after completing this lap, not the age on starting it."""
    if (
        stint.tyre_age_at_start is None
        or completed_lap < stint.lap_start
        or (stint.lap_end is not None and completed_lap > stint.lap_end)
    ):
        return None
    return stint.tyre_age_at_start + completed_lap - stint.lap_start + 1


def apply_history(
    history: SessionHistory, event: NormalizedRaceEvent, *, neutralization: str = "unknown"
) -> SessionHistory:
    return _apply_history_owned(history.model_copy(deep=True), event, neutralization=neutralization)


def _apply_history_owned(
    state: SessionHistory, event: NormalizedRaceEvent, *, neutralization: str = "unknown"
) -> SessionHistory:
    """Only for exclusively owned candidates; never pass a publicly borrowed tree.

    The pure wrapper above and private reconstruction share all retention rules.
    A failed candidate is discarded, not reused as an acknowledged prefix.
    """
    if event.session_key != state.session_key:
        raise ValueError("History session mismatch")
    if event.sequence_number <= state.sequence or event.event_origin != EventOrigin.SOURCE_FACT:
        return state
    state.sequence = event.sequence_number
    payload = event.payload
    evidence = FactReference(
        event_id=event.id,
        sequence=event.sequence_number,
        observed_at=event.event_time,
        source=event.source,
    )
    if event.event_type in {RaceEventType.WEATHER_UPDATE, RaceEventType.WEATHER_CHANGE}:
        rainfall = payload.get("rainfall")
        sample = WeatherObservation(
            air_temperature=finite_number(payload.get("air_temperature"), -80, 80),
            track_temperature=finite_number(payload.get("track_temperature"), -80, 100),
            humidity=finite_number(payload.get("humidity"), 0, 100),
            rainfall=bool(rainfall) if rainfall in (0, 1) else None,
            wind_speed=finite_number(payload.get("wind_speed"), 0, 150),
            wind_direction=finite_number(payload.get("wind_direction"), 0, 359),
            evidence=evidence,
        )
        state.weather = [
            row for row in state.weather if row.evidence.observed_at != event.event_time
        ]
        state.weather.append(sample)
        state.weather.sort(key=lambda row: (row.evidence.observed_at, row.evidence.sequence))
        state.weather_history_truncated |= len(state.weather) > 60
        state.weather = state.weather[-60:]
        return state
    if event.event_type not in {
        RaceEventType.LAP_COMPLETED,
        RaceEventType.LAP_DELETED,
        RaceEventType.PIT_STOP,
        RaceEventType.STINT_UPDATE,
    }:
        return state
    number = event.primary_driver_number or (
        event.driver_numbers[0] if event.driver_numbers else None
    )
    lap_number = _integer(event.lap_number or payload.get("lap_number"))
    if event.event_type == RaceEventType.LAP_DELETED and (
        number is None or number <= 0 or lap_number is None
    ):
        _note_unresolved_deletion(state)
        return state
    if number is None or number <= 0:
        return state
    key = str(number)
    if key not in state.drivers:
        if len(state.drivers) >= 64:
            state.driver_history_truncated = True
            if event.event_type == RaceEventType.LAP_DELETED:
                _note_unresolved_deletion(state)
            return state
        state.drivers[key] = DriverHistory()
    driver = state.drivers[key]
    if event.event_type == RaceEventType.STINT_UPDATE:
        stint_number = _integer(payload.get("stint_number"))
        start = _integer(payload.get("lap_start"))
        end = _integer(payload.get("lap_end"))
        if stint_number is None or start is None or (end is not None and end < start):
            return state
        compound = str(payload.get("compound") or "").strip().upper()
        stint = StintObservation(
            stint_number=stint_number,
            lap_start=start,
            lap_end=end,
            compound=compound
            if compound in {"SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET"}
            else None,
            tyre_age_at_start=_integer(payload.get("tyre_age_at_start"), 0),
            evidence=evidence,
        )
        stints = [row for row in driver.stints if row.stint_number != stint_number] + [stint]
        stints.sort(key=lambda row: row.stint_number)
        state.stint_history_truncated |= len(stints) > 24
        driver.stints = stints[-24:]
    elif event.event_type == RaceEventType.LAP_DELETED:
        target = next((row for row in driver.laps if row.lap_number == lap_number), None)
        if lap_number is None:
            _note_unresolved_deletion(state)
        else:
            if target is None:
                target = LapObservation(
                    lap_number=lap_number, evidence=evidence, exclusions=["missing_duration"]
                )
                laps = [*driver.laps, target]
                laps.sort(key=lambda row: row.lap_number)
                state.lap_history_truncated |= len(laps) > 120
                driver.laps = laps[-120:]
            target.deleted = True
            target.deletion_evidence = evidence
    elif lap_number is not None and event.event_type == RaceEventType.LAP_COMPLETED:
        previous = next((row for row in driver.laps if row.lap_number == lap_number), None)
        duration = finite_number(payload.get("lap_duration"), 0.001, 3600)
        exclusions = []
        if duration is None:
            exclusions.append("missing_duration")
        if payload.get("is_pit_out_lap") is True:
            exclusions.append("pit_out")
        if any(pit.lap_number == lap_number for pit in driver.pits):
            exclusions.append("pit_in")
        if neutralization not in {"green", "unknown"}:
            exclusions.append("neutralized")
        elif neutralization == "unknown":
            exclusions.append("control_unknown")
        if previous is not None:
            exclusions = [
                reason for reason in exclusions if reason not in {"neutralized", "control_unknown"}
            ]
            exclusions.extend(
                reason
                for reason in previous.exclusions
                if reason in {"neutralized", "control_unknown"}
            )
            if "pit_out" in previous.exclusions and "pit_out" not in exclusions:
                exclusions.append("pit_out")
        lap = LapObservation(
            lap_number=lap_number,
            duration_seconds=duration,
            sectors_seconds=[
                finite_number(payload.get(f"duration_sector_{i}"), 0.001, 1200) for i in range(1, 4)
            ],
            evidence=evidence,
            exclusions=exclusions,
            deleted=previous.deleted if previous else payload.get("deleted") is True,
            deletion_evidence=previous.deletion_evidence if previous else None,
            phase=(
                payload.get("session_phase")
                if payload.get("session_phase") in {"Q1", "Q2", "Q3", "SQ1", "SQ2", "SQ3"}
                else previous.phase
                if previous
                else None
            ),
        )
        if previous is not None:
            for field in (
                "interval_start",
                "interval_end",
                "interval_authority",
                "interval_evidence",
                "interval_limitations",
                "control_evidence",
            ):
                setattr(lap, field, getattr(previous, field))
        start = _aware_datetime(payload.get("date_start"))
        # OpenF1 documents date_start as approximate. Neither a receive time nor
        # a sector duration supplies a complete lap interval.
        if start is not None and duration is not None:
            try:
                end = start + timedelta(seconds=duration)
            except (OverflowError, ValueError):
                pass  # Unknown interval, or retain this identity's prior grounding.
            else:
                lap.interval_start = start
                lap.interval_end = end
                lap.interval_authority = "approximate_start_plus_complete_duration"
                lap.interval_evidence = [evidence]
                lap.interval_limitations = ["provider_lap_start_approximate"]
        laps = [row for row in driver.laps if row.lap_number != lap_number] + [lap]
        laps.sort(key=lambda row: row.lap_number)
        state.lap_history_truncated |= len(laps) > 120
        driver.laps = laps[-120:]
    elif lap_number is not None and event.event_type == RaceEventType.PIT_STOP:
        pit = PitObservation(
            lap_number=lap_number,
            lane_seconds=finite_number(
                payload.get("lane_duration", payload.get("pit_duration")), 0.001, 3600
            ),
            stationary_seconds=finite_number(payload.get("stop_duration"), 0.001, 3600),
            evidence=evidence,
        )
        pits = [row for row in driver.pits if row.lap_number != lap_number] + [pit]
        pits.sort(key=lambda row: row.lap_number)
        state.pit_history_truncated |= len(pits) > 24
        driver.pits = pits[-24:]
        for row in driver.laps:
            if row.lap_number == lap_number and "pit_in" not in row.exclusions:
                row.exclusions.append("pit_in")
    return state


def _aware_datetime(value: object) -> datetime | None:
    if not isinstance(value, (str, datetime)):
        return None
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        return result if result.tzinfo is not None and result.utcoffset() is not None else None
    except ValueError:
        return None
