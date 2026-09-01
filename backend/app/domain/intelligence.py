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


class RaceIntelligenceConfig(BaseModel):
    overtake_confirmation_seconds: float = Field(default=2.0, ge=0)
    overtake_confirmation_samples: int = Field(default=2, ge=1)
    overtake_max_interval_seconds: float = Field(default=2.5, gt=0)
    battle_start_interval_seconds: float = Field(default=2.0, gt=0)
    battle_start_samples: int = Field(default=3, ge=1)
    battle_intense_interval_seconds: float = Field(default=1.0, gt=0)
    battle_end_interval_seconds: float = Field(default=3.0, gt=0)
    battle_end_samples: int = Field(default=3, ge=1)
    battle_trend_window: int = Field(default=5, ge=3, le=20)
    battle_trend_minimum_change: float = Field(default=0.15, ge=0)
    proximity_exit_seconds: float = Field(default=1.2, gt=0)
    event_cooldown_seconds: float = Field(default=20.0, ge=0)


class OvertakeContext(BaseModel):
    session_type: str
    observed_at: datetime
    interval_before: float | None = None
    pit_data_available: bool = False
    location_available: bool = False
    both_running: bool = True


class OvertakeCandidate(BaseModel):
    session_key: str
    driver_number: int
    target_driver_number: int
    position_before: int
    position_after: int
    first_observed_at: datetime
    last_observed_at: datetime
    first_sequence: int
    last_sequence: int
    samples: int = 1
    interval_before: float | None = None


class BattleStatus(StrEnum):
    POTENTIAL = "POTENTIAL"
    ACTIVE = "ACTIVE"
    INTENSE = "INTENSE"
    RESOLVED = "RESOLVED"


class BattleTrend(StrEnum):
    CLOSING = "CLOSING"
    STABLE = "STABLE"
    FALLING_BACK = "FALLING_BACK"


class BattleIntensity(StrEnum):
    BUILDING = "BUILDING"
    CLOSE = "CLOSE"
    INTENSE = "INTENSE"


class BattleState(BaseModel):
    id: str
    session_key: str
    lead_driver_number: int
    chasing_driver_number: int
    lead_position: int
    chasing_position: int
    interval_seconds: float
    closest_interval_seconds: float
    interval_history: list[float] = Field(default_factory=list, max_length=20)
    started_at: datetime
    last_updated_at: datetime
    trend: BattleTrend = BattleTrend.STABLE
    intensity: BattleIntensity = BattleIntensity.BUILDING
    status: BattleStatus = BattleStatus.POTENTIAL
    within_one_second: bool = False
    drs_status: str = "UNAVAILABLE"
    tyre_context: dict[str, object] = Field(default_factory=dict)
    lap_number: int | None = None
    train_size: int = 2
    close_samples: int = Field(default=1, exclude=True)
    end_samples: int = Field(default=0, exclude=True)
    resolution_reason: str | None = None


class BattleUpdate(BaseModel):
    battle: BattleState
    event_type: object | None = None
