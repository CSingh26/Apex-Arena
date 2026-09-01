# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class PositionChangeCause(StrEnum):
    ON_TRACK_CANDIDATE = "ON_TRACK_CANDIDATE"
    PIT_CYCLE = "PIT_CYCLE"
    RETIREMENT_INHERITANCE = "RETIREMENT_INHERITANCE"
    PENALTY_OR_CLASSIFICATION = "PENALTY_OR_CLASSIFICATION"
    TIMING_CORRECTION = "TIMING_CORRECTION"
    LAPPED_ORDERING = "LAPPED_ORDERING"
    UNKNOWN = "UNKNOWN"


class PositionState(BaseModel):
    driver_number: int
    observed_position: int
    confirmed_position: int
    previous_confirmed_position: int | None = None
    start_position: int
    previous_lap_position: int | None = None
    status: str = "RUNNING"
    in_pit: bool = False
    last_observed_at: datetime
    last_sequence: int
    context: dict[str, object] = Field(default_factory=dict)


class PositionChange(BaseModel):
    session_key: str
    driver_number: int
    related_driver_numbers: list[int] = Field(default_factory=list)
    position_before: int
    position_after: int
    cause: PositionChangeCause
    observed_at: datetime
    source_sequence: int
    batch_key: str

    @property
    def position_delta(self) -> int:
        return self.position_before - self.position_after

