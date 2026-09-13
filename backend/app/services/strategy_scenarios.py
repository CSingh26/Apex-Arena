# SPDX-License-Identifier: AGPL-3.0-only
"""Conditional strategy arithmetic; outputs are scenarios, not outcome forecasts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, FiniteFloat, ValidationError

from app.domain.strategy import FactReference, PitLossEstimate

MAX_TIMING_AGE_SECONDS = 30
# Deliberately conservative cross-row alignment policy; it is not measured provider cadence.
MAX_TIMING_ALIGNMENT_SKEW_SECONDS = 5
MAX_REMAINING_LAPS = 100_000


class StrategyTiming(BaseModel):
    driver_number: int = Field(ge=1, le=999)
    gap_to_leader_seconds: FiniteFloat = Field(ge=0, le=7200)
    lap_number: int = Field(ge=1, le=100_000)
    evidence: FactReference


class PitWindowScenario(BaseModel):
    availability: Literal["available", "partial", "insufficient_evidence"] = "insufficient_evidence"
    projected_gap_range_seconds: tuple[FiniteFloat, FiniteFloat] | None = None
    rejoin_position_range: tuple[int, int] | None = None
    traffic_driver_numbers: list[int] = Field(default_factory=list, max_length=64)
    confidence: Literal["low"] = "low"
    assumptions: list[str] = Field(
        default_factory=lambda: [
            "field_gaps_held_constant",
            "observed_pit_loss_repeats",
            "traffic_window_is_not_pass_probability",
        ]
    )
    limitations: list[str] = Field(default_factory=list)
    evidence: list[FactReference] = Field(default_factory=list, max_length=71)


class ExtraStopScenario(BaseModel):
    required_gain_seconds_per_lap: FiniteFloat | None = None
    required_gain_range_seconds_per_lap: tuple[FiniteFloat, FiniteFloat] | None = None
    confidence: Literal["low"] = "low"
    assumptions: list[str] = Field(
        default_factory=lambda: [
            "observed_pit_loss_repeats",
            "fresh_tyre_pace_not_predicted",
            "constant_gain_over_remaining_laps",
            "traffic_and_stop_execution_can_change",
        ]
    )
    evidence: list[FactReference] = Field(default_factory=list, max_length=6)


def _validated_loss(loss: PitLossEstimate) -> PitLossEstimate | None:
    """Revalidate model-copy/list-mutation bypasses at the arithmetic boundary."""

    try:
        candidate = PitLossEstimate.model_validate(loss.model_dump(warnings=False))
    except (ValidationError, TypeError, ValueError, OverflowError):
        return None
    return candidate if candidate.availability == "available" else None


def _fresh(timing: StrategyTiming, as_of: datetime) -> bool:
    if timing.evidence.observed_at.tzinfo is None or as_of.tzinfo is None:
        return False
    return 0 <= (as_of - timing.evidence.observed_at).total_seconds() <= MAX_TIMING_AGE_SECONDS


def _aligned(left: StrategyTiming, right: StrategyTiming) -> bool:
    return (
        abs((left.evidence.observed_at - right.evidence.observed_at).total_seconds())
        <= MAX_TIMING_ALIGNMENT_SKEW_SECONDS
    )


def project_pit_window(
    driver: StrategyTiming,
    field: list[StrategyTiming],
    loss: PitLossEstimate,
    *,
    as_of: datetime,
    complete_field: bool,
    neutralization: str = "green",
) -> PitWindowScenario:
    result = PitWindowScenario()
    if neutralization != "green":
        result.limitations.append("matching_neutralized_pit_loss_unavailable")
        return result
    validated_loss = _validated_loss(loss)
    if len(field) > 64 or validated_loss is None or not _fresh(driver, as_of):
        return result
    loss = validated_loss
    others = [row for row in field if row.driver_number != driver.driver_number]
    eligible = {
        row.driver_number: row
        for row in others
        if row.lap_number == driver.lap_number and _fresh(row, as_of) and _aligned(driver, row)
    }
    if any(
        row.lap_number == driver.lap_number and _fresh(row, as_of) and not _aligned(driver, row)
        for row in others
    ):
        result.limitations.append("timing_observations_not_aligned")
    complete = complete_field and len(eligible) == len(others) and bool(others)
    lower = driver.gap_to_leader_seconds + loss.lower_seconds
    upper = driver.gap_to_leader_seconds + loss.upper_seconds
    result.projected_gap_range_seconds = (lower, upper)
    result.availability = "available" if complete else "partial"
    result.traffic_driver_numbers = sorted(
        row.driver_number
        for row in eligible.values()
        if lower - 2 <= row.gap_to_leader_seconds <= upper + 2
    )
    if complete:
        result.rejoin_position_range = (
            1 + sum(row.gap_to_leader_seconds < lower for row in eligible.values()),
            1 + sum(row.gap_to_leader_seconds <= upper for row in eligible.values()),
        )
    else:
        result.limitations.append("incomplete_comparable_field")
    result.evidence = [
        driver.evidence,
        *loss.evidence,
        *(row.evidence for row in eligible.values()),
    ]
    return result


def extra_stop_break_even(
    loss: PitLossEstimate, *, laps_remaining: int | None
) -> ExtraStopScenario:
    result = ExtraStopScenario()
    validated_loss = _validated_loss(loss)
    if (
        isinstance(laps_remaining, bool)
        or not isinstance(laps_remaining, int)
        or not 1 <= laps_remaining <= MAX_REMAINING_LAPS
        or validated_loss is None
    ):
        return result
    loss = validated_loss
    result.required_gain_seconds_per_lap = loss.seconds / laps_remaining
    result.required_gain_range_seconds_per_lap = (
        loss.lower_seconds / laps_remaining,
        loss.upper_seconds / laps_remaining,
    )
    result.evidence = list(loss.evidence)
    return result
