# SPDX-License-Identifier: AGPL-3.0-only
"""Serializable bounded factual inputs for deterministic strategy estimates."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

DurationSeconds = Annotated[FiniteFloat, Field(gt=0, le=3600)]
SectorSeconds = Annotated[FiniteFloat, Field(gt=0, le=1200)]
NonNegativeSeconds = Annotated[FiniteFloat, Field(ge=0, le=14_400)]
SignedSeconds = Annotated[FiniteFloat, Field(ge=-3600, le=3600)]
AirTemperature = Annotated[FiniteFloat, Field(ge=-80, le=80)]
TrackTemperature = Annotated[FiniteFloat, Field(ge=-80, le=100)]
Humidity = Annotated[FiniteFloat, Field(ge=0, le=100)]
WindSpeed = Annotated[FiniteFloat, Field(ge=0, le=150)]
WindDirection = Annotated[FiniteFloat, Field(ge=0, le=359)]


class StrategyModel(BaseModel):
    model_config = ConfigDict(validate_assignment=True)


class FactReference(StrategyModel):
    event_id: UUID
    sequence: int = Field(ge=1)
    observed_at: datetime
    source: str


class PitLossEvidenceRoles(StrategyModel):
    """Method roles only; callers must resolve facts to one compatible session/regime."""

    baseline_laps: tuple[FactReference, FactReference, FactReference]
    in_lap: FactReference
    out_lap: FactReference
    pit: FactReference

    def ordered(self) -> list[FactReference]:
        return [*self.baseline_laps, self.in_lap, self.out_lap, self.pit]


class LapObservation(StrategyModel):
    lap_number: int = Field(ge=1, le=100_000)
    duration_seconds: DurationSeconds | None = None
    sectors_seconds: list[SectorSeconds | None] = Field(default_factory=list, max_length=3)
    evidence: FactReference
    deleted: bool = False
    deletion_evidence: FactReference | None = None
    exclusions: list[str] = Field(default_factory=list, max_length=8)
    phase: str | None = Field(default=None, max_length=32)
    interval_start: datetime | None = None
    interval_end: datetime | None = None
    interval_authority: Literal[
        "unknown", "approximate_provider_interval", "approximate_start_plus_complete_duration"
    ] = "unknown"
    interval_evidence: list[FactReference] = Field(default_factory=list, max_length=2)
    interval_limitations: list[str] = Field(default_factory=list, max_length=8)
    control_evidence: list[FactReference] = Field(default_factory=list, max_length=3)


class StintObservation(StrategyModel):
    stint_number: int = Field(ge=1, le=100_000)
    compound: str | None = None
    lap_start: int = Field(ge=1, le=100_000)
    lap_end: int | None = Field(default=None, ge=1, le=100_000)
    tyre_age_at_start: int | None = Field(default=None, ge=0, le=100_000)
    evidence: FactReference


class PitObservation(StrategyModel):
    lap_number: int = Field(ge=1, le=100_000)
    lane_seconds: DurationSeconds | None = None
    stationary_seconds: DurationSeconds | None = None
    evidence: FactReference


class WeatherObservation(StrategyModel):
    air_temperature: AirTemperature | None = None
    track_temperature: TrackTemperature | None = None
    humidity: Humidity | None = None
    rainfall: bool | None = None
    wind_speed: WindSpeed | None = None
    wind_direction: WindDirection | None = None
    evidence: FactReference


class DriverHistory(StrategyModel):
    laps: list[LapObservation] = Field(default_factory=list, max_length=120)
    stints: list[StintObservation] = Field(default_factory=list, max_length=24)
    pits: list[PitObservation] = Field(default_factory=list, max_length=24)

    @model_validator(mode="after")
    def unique_lap_identity(self) -> Self:
        lap_numbers = [lap.lap_number for lap in self.laps]
        if len(lap_numbers) != len(set(lap_numbers)):
            raise ValueError("duplicate lap number")
        return self


class SessionHistory(StrategyModel):
    session_key: str
    sequence: int = 0
    drivers: dict[str, DriverHistory] = Field(default_factory=dict, max_length=64)
    weather: list[WeatherObservation] = Field(default_factory=list, max_length=60)
    lap_history_truncated: bool = False
    driver_history_truncated: bool = False
    stint_history_truncated: bool = False
    pit_history_truncated: bool = False
    weather_history_truncated: bool = False
    unresolved_deletions: int = Field(default=0, ge=0, le=100_000)


class PaceEstimate(StrategyModel):
    availability: Literal["available", "insufficient_evidence"] = "insufficient_evidence"
    stint_number: int | None = None
    representative_seconds: DurationSeconds | None = None
    observed_range_seconds: tuple[DurationSeconds, DurationSeconds] | None = None
    sample_laps: list[int] = Field(default_factory=list, max_length=12)
    excluded_laps: list[int] = Field(default_factory=list, max_length=120)
    evidence: list[FactReference] = Field(default_factory=list, max_length=13)
    pace_drift_seconds_per_lap: SignedSeconds | None = None
    drift_range_seconds_per_lap: tuple[SignedSeconds, SignedSeconds] | None = None
    expected_tyre_life_laps: int | None = None
    confidence: Literal["low"] = "low"
    limitations: list[str] = Field(
        default_factory=lambda: [
            "fuel_and_traffic_not_isolated",
            "weather_not_isolated",
            "observed_pace_not_causal_tyre_degradation",
            "no_tyre_life_model",
        ]
    )

    @model_validator(mode="after")
    def available_metrics_are_coherent(self) -> Self:
        if self.observed_range_seconds is not None:
            lower, upper = self.observed_range_seconds
            if lower > upper:
                raise ValueError("pace range must be ordered")
        if self.drift_range_seconds_per_lap is not None:
            lower, upper = self.drift_range_seconds_per_lap
            if lower > upper:
                raise ValueError("pace drift range must be ordered")
            if self.pace_drift_seconds_per_lap is not None and not (
                lower <= self.pace_drift_seconds_per_lap <= upper
            ):
                raise ValueError("pace drift must be within its range")
        if self.availability == "available":
            if self.representative_seconds is None or self.observed_range_seconds is None:
                raise ValueError("available pace requires complete metrics")
            lower, upper = self.observed_range_seconds
            if not lower <= self.representative_seconds <= upper:
                raise ValueError("representative pace must be within its range")
        return self


class PitLossEstimate(StrategyModel):
    availability: Literal["available", "insufficient_evidence"] = "insufficient_evidence"
    seconds: NonNegativeSeconds | None = None
    lower_seconds: NonNegativeSeconds | None = None
    upper_seconds: NonNegativeSeconds | None = None
    method: Literal["observed_two_lap_excess"] = "observed_two_lap_excess"
    confidence: Literal["low"] = "low"
    evidence: list[FactReference] = Field(default_factory=list, max_length=6)
    evidence_roles: PitLossEvidenceRoles | None = None
    limitations: list[str] = Field(
        default_factory=lambda: [
            "traffic_and_tyre_warmup_included",
            "range_is_sensitivity_not_probability",
            "not_transferable_to_neutralized_conditions",
            "evidence_session_and_regime_require_caller_resolution",
        ]
    )

    @model_validator(mode="after")
    def available_metrics_are_coherent(self) -> Self:
        values = (self.lower_seconds, self.seconds, self.upper_seconds)
        if self.availability == "available":
            if any(value is None for value in values):
                raise ValueError("available pit loss requires complete metrics")
            if len(self.evidence) != 6:
                raise ValueError("observed two-lap pit loss requires six evidence facts")
            if self.evidence_roles is None:
                raise ValueError("observed two-lap pit loss requires explicit evidence roles")
            role_evidence = self.evidence_roles.ordered()
            if role_evidence != self.evidence:
                raise ValueError("pit loss evidence roles must match ordered evidence")
            if (
                len({reference.event_id for reference in role_evidence}) != 6
                or len({reference.sequence for reference in role_evidence}) != 6
            ):
                raise ValueError("observed two-lap pit loss requires distinct evidence identities")
        if all(value is not None for value in values) and not values[0] <= values[1] <= values[2]:
            raise ValueError("pit loss range must be ordered")
        return self
