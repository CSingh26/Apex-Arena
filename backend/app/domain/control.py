# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.domain.strategy import FactReference

LifecycleValue = Literal["unknown", "scheduled", "delayed", "running", "suspended", "finished"]
NeutralizationValue = Literal[
    "unknown",
    "green",
    "safety_car",
    "safety_car_ending",
    "virtual_safety_car",
    "virtual_safety_car_ending",
    "red",
]


class ControlSemantics(BaseModel):
    lifecycle: LifecycleValue | None = None
    neutralization: NeutralizationValue | None = None
    drs_permission: Literal["enabled", "disabled"] | None = None
    flag: Literal["unknown", "green", "yellow", "double_yellow", "red"] | None = None
    sector: int | None = Field(default=None, ge=1, le=100)


class ControlObservation(BaseModel):
    value: str = "unknown"
    evidence: FactReference | None = None


class ControlTransition(FactReference):
    semantics: ControlSemantics


class CurrentControlProjection(BaseModel):
    session_key: str
    sequence: int = 0
    lifecycle: ControlObservation = Field(default_factory=ControlObservation)
    neutralization: ControlObservation = Field(default_factory=ControlObservation)
    drs_permission: ControlObservation = Field(default_factory=ControlObservation)
    track_flag: ControlObservation = Field(default_factory=ControlObservation)
    sector_flags: dict[str, ControlObservation] = Field(default_factory=dict, max_length=100)
    # A data-quality bit, not retained detail: once the bounded transition prefix
    # is evicted, control inference can no longer prove a clean racing interval.
    # Consumers need that caveat, so it travels with the public projection while
    # the transition list itself stays private to the deterministic projection.
    history_truncated: bool = False


class ControlProjection(CurrentControlProjection):
    history: list[ControlTransition] = Field(default_factory=list, max_length=120)
