# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.intelligence import BattleState
from app.domain.models import (
    EventConfidence,
    EventImportance,
    EventOrigin,
    NormalizedRaceEvent,
    RaceEventType,
)
from app.services.race_state import RaceState

RAW_SAMPLE_TYPES = {
    RaceEventType.DRIVER_UPDATE,
    RaceEventType.POSITION_SAMPLE,
    RaceEventType.INTERVAL_SAMPLE,
    RaceEventType.CAR_DATA_SAMPLE,
    RaceEventType.LOCATION_SAMPLE,
    RaceEventType.WEATHER_UPDATE,
}
DERIVED_AGENT_TYPES = {
    RaceEventType.OVERTAKE,
    RaceEventType.BATTLE_INTENSIFIED,
    RaceEventType.DRS_RANGE_ENTERED,
    RaceEventType.QUALIFYING_CUTOFF_CHANGE,
}
SOURCE_AGENT_TYPES = {
    RaceEventType.SESSION_START,
    RaceEventType.QUALIFYING_PHASE,
    RaceEventType.RACE_START,
    RaceEventType.LAP_COMPLETED,
    RaceEventType.POSITION_CHANGE,
    RaceEventType.OVERTAKE,
    RaceEventType.PIT_STOP,
    RaceEventType.TYRE_CHANGE,
    RaceEventType.FASTEST_LAP,
    RaceEventType.LAP_DELETED,
    RaceEventType.SESSION_RESULT,
    RaceEventType.SAFETY_CAR,
    RaceEventType.VIRTUAL_SAFETY_CAR,
    RaceEventType.RED_FLAG,
    RaceEventType.YELLOW_FLAG,
    RaceEventType.PENALTY,
    RaceEventType.RACE_CONTROL,
    RaceEventType.WEATHER_CHANGE,
    RaceEventType.RETIREMENT,
    RaceEventType.DRIVER_RETIRED,
    RaceEventType.SESSION_FINISH,
}
IMPORTANCE_RANK = {
    EventImportance.LOW: 0,
    EventImportance.NORMAL: 1,
    EventImportance.IMPORTANT: 2,
    EventImportance.MAJOR: 3,
    EventImportance.CRITICAL: 4,
}
CURATED_PAYLOAD_KEYS = {
    "normalized_session_type",
    "session_phase",
    "data_quality",
    "resolved_driver_name",
    "full_name",
    "driver_name",
    "broadcast_name",
    "driver_acronym",
    "acronym",
    "position",
    "previous_position",
    "lap_duration",
    "pit_duration",
    "duration",
    "compound",
    "pace_trend_seconds",
    "representative_laps",
    "message",
    "status",
    "rainfall",
    "cutoff_position",
    "entered_drop_zone",
    "left_drop_zone",
    "provisional_pole_driver",
    "best_lap",
}


class AgentEligibility:
    """Admit race meaning, never raw timing/location/telemetry samples."""

    def evaluate(self, event: NormalizedRaceEvent) -> bool:
        if event.event_type in RAW_SAMPLE_TYPES:
            return False
        if event.payload.get("stale") or event.payload.get("data_quality") == "stale":
            return False
        if event.event_origin is EventOrigin.DERIVED:
            return (
                event.confidence_level is not EventConfidence.LOW
                and IMPORTANCE_RANK[event.importance_level]
                >= IMPORTANCE_RANK[EventImportance.IMPORTANT]
                and event.event_type in DERIVED_AGENT_TYPES
            )
        return event.event_type in SOURCE_AGENT_TYPES


class AgentDriverFact(BaseModel):
    model_config = ConfigDict(frozen=True)

    driver_number: int
    full_name: str | None = None
    team_name: str | None = None
    position: int | None = None
    status: str | None = None
    in_pit: bool | None = None
    gap_to_leader: float | str | None = None
    interval: float | str | None = None
    latest_lap_duration: float | None = None
    best_lap_duration: float | None = None
    compound: str | None = None
    tyre_age_laps: int | None = None


class AgentBattleFact(BaseModel):
    model_config = ConfigDict(frozen=True)

    battle_id: str
    lead_driver_number: int
    chasing_driver_number: int
    lead_position: int
    chasing_position: int
    interval_seconds: float
    closest_interval_seconds: float
    trend: str
    intensity: str
    within_one_second: bool
    duration_seconds: float
    train_size: int


class AgentEventEnvelope(BaseModel):
    """One immutable, secret-safe fact set shared by every reacting agent."""

    model_config = ConfigDict(frozen=True)

    event_id: str
    event_sequence: int
    event_type: str
    event_origin: str
    session_key: str
    event_time: datetime
    session_type: str | None = None
    session_phase: str | None = None
    lap_number: int | None = None
    driver_numbers: list[int] = Field(default_factory=list)
    primary_driver_number: int | None = None
    secondary_driver_number: int | None = None
    position: int | None = None
    previous_position: int | None = None
    gap_seconds: float | None = None
    interval_seconds: float | None = None
    importance_level: str
    confidence_level: str
    drivers: dict[str, AgentDriverFact] = Field(default_factory=dict)
    battle: AgentBattleFact | None = None
    derivation_algorithm: str | None = None
    derivation_evidence: list[dict[str, Any]] = Field(default_factory=list)
    payload_facts: dict[str, Any] = Field(default_factory=dict)
    race_status: str | None = None
    race_current_lap: int | None = None
    data_quality: str = "partial"

    @classmethod
    def from_event(
        cls,
        event: NormalizedRaceEvent,
        state: RaceState | None,
        battle: BattleState | None = None,
    ) -> AgentEventEnvelope:
        numbers = list(event.driver_numbers)
        primary = event.primary_driver_number or (numbers[0] if numbers else None)
        secondary = event.secondary_driver_number or (numbers[1] if len(numbers) > 1 else None)
        if primary is not None and primary not in numbers:
            numbers.insert(0, primary)
        if secondary is not None and secondary not in numbers:
            numbers.append(secondary)
        selected_battle = battle or cls._battle_for(numbers, event, state)
        payload_facts = {
            key: value for key, value in event.payload.items() if key in CURATED_PAYLOAD_KEYS
        }
        drivers = {
            str(number): cls._driver_fact(number, state)
            for number in numbers
            if state is not None and str(number) in state.drivers
        }
        return cls(
            event_id=str(event.id),
            event_sequence=event.sequence_number,
            event_type=event.event_type.value,
            event_origin=event.event_origin.value,
            session_key=event.session_key,
            event_time=event.event_time,
            session_type=(
                state.session_type
                if state is not None and state.session_type
                else cls._text(event.payload.get("normalized_session_type"))
            ),
            session_phase=(
                state.current_phase
                if state is not None and state.current_phase
                else cls._text(event.payload.get("session_phase"))
            ),
            lap_number=event.lap_number,
            driver_numbers=numbers,
            primary_driver_number=primary,
            secondary_driver_number=secondary,
            position=event.position_after or cls._integer(event.payload.get("position")),
            previous_position=event.position_before
            or cls._integer(event.payload.get("previous_position")),
            gap_seconds=event.gap_seconds,
            interval_seconds=event.interval_seconds,
            importance_level=event.importance_level.value,
            confidence_level=event.confidence_level.value,
            drivers=drivers,
            battle=cls._battle_fact(selected_battle) if selected_battle else None,
            derivation_algorithm=event.derivation.algorithm if event.derivation else None,
            derivation_evidence=(
                [item.model_dump(mode="json") for item in event.derivation.evidence]
                if event.derivation
                else []
            ),
            payload_facts=payload_facts,
            race_status=state.status if state is not None else None,
            race_current_lap=state.current_lap if state is not None else None,
            data_quality=str(event.payload.get("data_quality") or "partial"),
        )

    def as_evidence(self) -> dict[str, Any]:
        evidence: dict[str, Any] = {
            "event_id": self.event_id,
            "event_sequence": self.event_sequence,
            "event_type": self.event_type,
            "event_origin": self.event_origin,
            "session_key": self.session_key,
            "event_time": self.event_time.isoformat(),
            "lap_number": self.lap_number,
            "driver_numbers": self.driver_numbers,
            "primary_driver_number": self.primary_driver_number,
            "secondary_driver_number": self.secondary_driver_number,
            "position": self.position,
            "previous_position": self.previous_position,
            "gap_seconds": self.gap_seconds,
            "interval_seconds": self.interval_seconds,
            "importance_level": self.importance_level,
            "confidence_level": self.confidence_level,
            "session_type": self.session_type,
            "session_phase": self.session_phase,
            "race_status": self.race_status,
            "race_current_lap": self.race_current_lap,
            "relevant_driver_state": {
                number: driver.model_dump(mode="json", exclude_none=True)
                for number, driver in self.drivers.items()
            },
            "battle": self.battle.model_dump(mode="json") if self.battle else None,
            "derivation_algorithm": self.derivation_algorithm,
            "derivation_evidence": self.derivation_evidence,
        }
        evidence.update(self.payload_facts)
        return {key: value for key, value in evidence.items() if value is not None}

    @classmethod
    def _driver_fact(cls, number: int, state: RaceState | None) -> AgentDriverFact:
        assert state is not None
        driver = state.drivers[str(number)]
        compound = cls._text(driver.stint.get("compound") or driver.stint.get("tyre_compound"))
        start = cls._integer(driver.stint.get("lap_start") or driver.stint.get("start_lap"))
        tyre_age = (
            state.current_lap - start + 1
            if start is not None and state.current_lap is not None and state.current_lap >= start
            else None
        )
        return AgentDriverFact(
            driver_number=number,
            full_name=driver.full_name or driver.broadcast_name,
            team_name=driver.team_name,
            position=driver.position,
            status=driver.status,
            in_pit=driver.in_pit,
            gap_to_leader=driver.gap_to_leader,
            interval=driver.interval,
            latest_lap_duration=driver.latest_lap_duration,
            best_lap_duration=driver.best_lap_duration,
            compound=compound,
            tyre_age_laps=tyre_age,
        )

    @staticmethod
    def _battle_for(
        numbers: list[int],
        event: NormalizedRaceEvent,
        state: RaceState | None,
    ) -> BattleState | None:
        payload_battle = event.payload.get("battle")
        if isinstance(payload_battle, dict):
            try:
                return BattleState.model_validate(payload_battle)
            except ValueError:
                pass
        if state is None:
            return None
        return next(
            (
                battle
                for battle in state.current_battles
                if battle.lead_driver_number in numbers
                or battle.chasing_driver_number in numbers
            ),
            None,
        )

    @staticmethod
    def _battle_fact(battle: BattleState) -> AgentBattleFact:
        return AgentBattleFact(
            battle_id=battle.id,
            lead_driver_number=battle.lead_driver_number,
            chasing_driver_number=battle.chasing_driver_number,
            lead_position=battle.lead_position,
            chasing_position=battle.chasing_position,
            interval_seconds=battle.interval_seconds,
            closest_interval_seconds=battle.closest_interval_seconds,
            trend=battle.trend.value,
            intensity=battle.intensity.value,
            within_one_second=battle.within_one_second,
            duration_seconds=max(
                0,
                (battle.last_updated_at - battle.started_at).total_seconds(),
            ),
            train_size=battle.train_size,
        )

    @staticmethod
    def _integer(value: object) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _text(value: object) -> str | None:
        text = str(value or "").strip()
        return text or None
