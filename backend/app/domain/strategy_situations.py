# SPDX-License-Identifier: AGPL-3.0-only
"""Bounded, self-contained strategy assertions, separate from factual history."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, FiniteFloat, model_validator

Code = Annotated[str, Field(min_length=1, max_length=80)]
SessionKey = Annotated[str, Field(min_length=1, max_length=128)]
SemanticIdentity = Annotated[str, Field(min_length=1, max_length=256)]
FRAME_BYTES = 65_536
PAYLOAD_BYTES = 32_768
EVENT_BYTES = 65_536
HEADER_BYTES = 8_192


def encoded(value: BaseModel | dict) -> bytes:
    data = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )


class BoundedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")


class StrategySituationKind(StrEnum):
    STINT_DIVERGENCE = "stint_divergence"
    RELATIVE_PACE = "relative_pace"
    PIT_WINDOW = "pit_window"
    UNDERCUT_CONDITION = "undercut_condition"
    OVERCUT_CONDITION = "overcut_condition"
    NEUTRALIZED_PIT_CONTEXT = "neutralized_pit_context"
    EXTRA_STOP_CONSEQUENCE = "extra_stop_consequence"
    WEATHER_CHANGE = "weather_change"


class StrategyEvidence(BoundedModel):
    event_id: UUID
    sequence: int = Field(ge=1)
    observed_at: AwareDatetime
    source: Code
    session_key: SessionKey
    role: Literal[
        "weather",
        "stint",
        "lap",
        "pit",
        "position",
        "interval",
        "team",
        "control",
        "drs",
        "distance",
        "deletion",
    ]
    family: Code
    driver_number: int | None = Field(default=None, ge=1)
    lap_number: int | None = Field(default=None, ge=1)
    stint_number: int | None = Field(default=None, ge=1)
    basis: Literal["observed", "approximate_lap_interval", "inferred"] = "observed"

    @property
    def key(self) -> str:
        return f"{self.event_id}:{self.role}"


class DriverTyreContext(BoundedModel):
    driver_number: int = Field(ge=1)
    compound: Code | None = None
    stint_number: int | None = Field(default=None, ge=1)
    age_laps: int | None = Field(default=None, ge=0)
    age_basis: Code = "unknown"
    stop_count: int = Field(default=0, ge=0, le=24)
    stop_count_basis: Literal["complete", "retained_lower_bound"] = "retained_lower_bound"


class PaceWindow(BoundedModel):
    driver_number: int = Field(ge=1)
    stint_number: int = Field(ge=1)
    median_seconds: FiniteFloat
    range_seconds: tuple[FiniteFloat, FiniteFloat]
    sample_laps: list[int] = Field(min_length=3, max_length=12)


class RelativePaceContext(BoundedModel):
    first: PaceWindow
    second: PaceWindow
    difference_seconds: FiniteFloat
    range_seconds: tuple[FiniteFloat, FiniteFloat]
    shared_conditions: Literal["overlapping_green_samples"] = "overlapping_green_samples"


class PitWindowContext(BoundedModel):
    loss_seconds: FiniteFloat
    loss_range_seconds: tuple[FiniteFloat, FiniteFloat]
    projected_gap_seconds: tuple[FiniteFloat, FiniteFloat]
    traffic: list[int] = Field(default_factory=list, max_length=64)
    rank_range: tuple[int, int] | None = None
    timing_basis: Literal["observed", "approximate_lap_interval"] = "observed"


class SituationPayload(BoundedModel):
    """Typed optional case fields; kind-specific admission is enforced by the producer."""

    tyres: list[DriverTyreContext] = Field(default_factory=list, max_length=2)
    age_offset_laps: int | None = None
    same_reported_team: bool | None = None
    plan: Literal["unknown"] = "unknown"
    pace: RelativePaceContext | None = None
    pit_window: PitWindowContext | None = None
    gap_seconds: FiniteFloat | None = None
    required_gain_seconds: FiniteFloat | None = None
    required_gain_range_seconds: tuple[FiniteFloat, FiniteFloat] | None = None
    required_average_gain_seconds: tuple[FiniteFloat, FiniteFloat] | None = None
    remaining_laps: int | None = Field(default=None, ge=1)
    pit_anchor: UUID | None = None
    clean_laps_since_pit: int | None = Field(default=None, ge=1, le=5)
    neutralization: Code | None = None
    numeric_saving: None = None
    rainfall_before: bool | None = None
    rainfall_now: bool | None = None
    track_temperature_change: FiniteFloat | None = None
    air_temperature_change: FiniteFloat | None = None
    wind_speed_change: FiniteFloat | None = None
    wind_direction_change: FiniteFloat | None = None
    outcome: Literal["unknown"] = "unknown"
    new_tyre_pace: Literal["unknown"] = "unknown"
    warmup: Literal["unknown"] = "unknown"
    rival_stop_timing: Literal["unknown"] = "unknown"


class StrategySituation(BoundedModel):
    situation_id: UUID
    revision_id: UUID
    kind: StrategySituationKind
    status: Literal["active", "withdrawn"] = "active"
    transition: Literal["opened", "revised", "withdrawn"] = "opened"
    superseded_revision_id: UUID | None = None
    participants: list[int] = Field(default_factory=list, max_length=2)
    source_anchor: UUID
    source_sequence: int = Field(ge=1)
    session_key: SessionKey
    sequence: int = Field(ge=1)
    history_sequence: int = Field(ge=0)
    analysis_time: AwareDatetime
    semantic_identity: SemanticIdentity
    availability: Literal["available", "partial", "unavailable"] = "partial"
    payload: SituationPayload
    evidence_keys: list[Code] = Field(default_factory=list, max_length=96)
    assumptions: list[Code] = Field(default_factory=list, max_length=12)
    limitations: list[Code] = Field(default_factory=list, max_length=12)
    observation_confidence: Literal["observed", "qualified"] = "qualified"
    implication_uncertainty: Literal["unknown"] = "unknown"


class Capability(BoundedModel):
    availability: Literal["available", "partial", "unavailable", "omitted"] = "unavailable"
    reason: Code = "missing_evidence"


def capabilities() -> dict[str, Capability]:
    result = {kind.value: Capability() for kind in StrategySituationKind}
    result["extra_stop_consequence"] = Capability(reason="missing_authoritative_remaining_distance")
    return result


class StrategyFrame(BoundedModel):
    session_key: SessionKey
    sequence: int = Field(ge=0)
    history_sequence: int = Field(ge=0)
    history_event_id: UUID | None = None
    analysis_time: AwareDatetime
    semantic_identity: SemanticIdentity
    clock_basis: Literal["monotonic_consumed_source"] = "monotonic_consumed_source"
    projection_status: Literal["acknowledged_at_cursor", "unavailable"] = "acknowledged_at_cursor"
    capabilities: dict[str, Capability] = Field(
        default_factory=capabilities, min_length=8, max_length=8
    )
    situations: list[StrategySituation] = Field(default_factory=list, max_length=16)
    evidence: dict[str, StrategyEvidence] = Field(default_factory=dict, max_length=512)
    situations_truncated: bool = False
    omitted_situations: int = Field(default=0, ge=0, le=2048)
    suppressed_events: int = Field(default=0, ge=0, le=2048)
    limitations: list[Code] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def closure_and_size(self):
        if self.history_sequence > self.sequence:
            raise ValueError("future history cursor")
        if set(self.capabilities) != {kind.value for kind in StrategySituationKind}:
            raise ValueError("exactly eight capabilities required")
        used = {key for situation in self.situations for key in situation.evidence_keys}
        if used != set(self.evidence):
            raise ValueError("evidence closure must be exact")
        for key, ref in self.evidence.items():
            if (
                key != ref.key
                or ref.session_key != self.session_key
                or ref.sequence > self.sequence
                or ref.observed_at > self.analysis_time
            ):
                raise ValueError("invalid evidence authority")
        for item in self.situations:
            if (
                item.session_key != self.session_key
                or item.sequence > self.sequence
                or item.history_sequence > self.history_sequence
                or item.source_sequence > item.sequence
                or item.analysis_time > self.analysis_time
                or item.semantic_identity != self.semantic_identity
            ):
                raise ValueError("invalid situation authority")
        data = self.model_dump(mode="json")
        if len(encoded(data)) > FRAME_BYTES:
            raise ValueError("frame byte_budget")
        data.update(situations=[], evidence={})
        if len(encoded(data)) > HEADER_BYTES:
            raise ValueError("header byte_budget")
        return self


class StrategyTransition(BoundedModel):
    schema_version: Literal["strategy-v1"] = "strategy-v1"
    situation: StrategySituation
    evidence: dict[str, StrategyEvidence] = Field(max_length=96)

    @model_validator(mode="after")
    def closure(self):
        item = self.situation
        StrategyFrame(
            session_key=item.session_key,
            sequence=item.sequence,
            history_sequence=item.history_sequence,
            analysis_time=item.analysis_time,
            semantic_identity=item.semantic_identity,
            situations=[item],
            evidence=self.evidence,
        )
        if len(encoded(self)) > PAYLOAD_BYTES:
            raise ValueError("payload byte_budget")
        return self


class BattleProminence(BoundedModel):
    interval: int = Field(default=0, ge=0, le=30)
    persistence: int = Field(default=0, ge=0, le=15)
    closing: int = Field(default=0, ge=0, le=15)
    lead_position: int = Field(default=0, ge=0, le=10)
    train: int = Field(default=0, ge=0, le=10)
    remaining_distance: int = Field(default=0, ge=0, le=10)
    team_relevance: int = Field(default=0, ge=0, le=5)
    strategy_relevance: int = Field(default=0, ge=0, le=5)
    score: int = Field(default=0, ge=0, le=100)
    basis: Literal["timing_pressure", "green_timing_pressure", "not_racing"] = "timing_pressure"


class BattleContext(BoundedModel):
    tyres: list[DriverTyreContext] = Field(default_factory=list, max_length=2)
    pace: RelativePaceContext | None = None
    duration_seconds: FiniteFloat | None = None
    closing: bool = False
    train_members: list[int] = Field(default_factory=list, max_length=64)
    same_reported_team: bool | None = None
    remaining_laps: int | None = None
    drs_permission: Code = "unknown"
    within_one_second: bool | None = None
    observed_wing_open: bool | None = None
    attempt_evidence: Literal["unavailable"] = "unavailable"
    championship_context: Literal["unavailable"] = "unavailable"
    prominence: BattleProminence = Field(default_factory=BattleProminence)
    evidence: dict[str, StrategyEvidence] = Field(default_factory=dict, max_length=96)
    limitations: list[Code] = Field(default_factory=list, max_length=12)


class StrategyDelta(BoundedModel):
    frame: StrategyFrame
    transitions: list[StrategyTransition] = Field(default_factory=list, max_length=2048)
    battle_contexts: dict[str, BattleContext] = Field(default_factory=dict, max_length=64)
