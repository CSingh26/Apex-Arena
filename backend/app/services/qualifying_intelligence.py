# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from math import ceil

from app.domain.intelligence import QualifyingState
from app.domain.models import (
    DerivationEvidence,
    EventConfidence,
    EventDerivation,
    EventImportance,
    EventOrigin,
    NormalizedRaceEvent,
    RaceEventType,
)
from app.services.race_state import RaceState

QUALIFYING_SESSIONS = {"QUALIFYING", "SPRINT_QUALIFYING"}


def qualifying_cutoff(field_size: int, phase: str | None) -> int | None:
    normalized = str(phase or "").upper()
    if normalized == "Q1":
        return 10 + ceil(max(0, field_size - 10) / 2)
    if normalized == "Q2":
        return 10
    return None


class QualifyingEngine:
    def __init__(self, *, cooldown_seconds: float = 20) -> None:
        self.cooldown_seconds = max(0.0, cooldown_seconds)
        self._states: dict[str, QualifyingState] = {}

    def state_for(self, session_key: str) -> QualifyingState | None:
        state = self._states.get(session_key)
        return state.model_copy(deep=True) if state is not None else None

    def reset_session(self, session_key: str) -> None:
        self._states.pop(session_key, None)

    def apply(
        self,
        event: NormalizedRaceEvent,
        race_state: RaceState,
    ) -> list[NormalizedRaceEvent]:
        if str(race_state.session_type or "").upper() not in QUALIFYING_SESSIONS:
            return []
        state = self._states.get(event.session_key)
        if state is None:
            state = self._initial_state(event.session_key, race_state)
            self._states[event.session_key] = state

        events: list[NormalizedRaceEvent] = []
        phase = str(event.payload.get("session_phase") or race_state.current_phase or "").upper()
        if phase and phase != state.phase:
            previous = state.phase
            state.phase = phase
            state.cutoff_position = qualifying_cutoff(state.field_size, phase)
            if previous is not None:
                events.append(
                    self._event(
                        event,
                        RaceEventType.SESSION_PHASE_CHANGE,
                        payload={"phase_before": previous, "phase_after": phase},
                    )
                )

        if event.event_type is RaceEventType.POSITION_SAMPLE and event.driver_numbers:
            events.extend(self._position_events(event, state))
        elif event.event_type is RaceEventType.LAP_COMPLETED and event.driver_numbers:
            events.extend(self._lap_events(event, state))
        return events

    def _position_events(
        self,
        event: NormalizedRaceEvent,
        state: QualifyingState,
    ) -> list[NormalizedRaceEvent]:
        driver = event.driver_numbers[0]
        position = self._positive_int(event.payload.get("position"))
        if position is None:
            return []
        previous = state.positions.get(driver)
        state.positions[driver] = position
        if previous is None:
            state.field_size = len(state.positions)
            state.cutoff_position = qualifying_cutoff(state.field_size, state.phase)
        events: list[NormalizedRaceEvent] = []
        cutoff = state.cutoff_position
        if previous is not None and cutoff is not None:
            was_drop = previous > cutoff
            is_drop = position > cutoff
            if was_drop != is_drop:
                events.append(
                    self._event(
                        event,
                        RaceEventType.QUALIFYING_CUTOFF_CHANGE,
                        driver=driver,
                        payload={
                            "movement": (
                                "INTO_DROP_ZONE" if is_drop else "OUT_OF_DROP_ZONE"
                            ),
                            "cutoff_position": cutoff,
                            "position_before": previous,
                            "position_after": position,
                            "session_phase": state.phase,
                        },
                    )
                )
        if position == 1 and state.provisional_pole_driver != driver:
            previous_pole = state.provisional_pole_driver
            state.provisional_pole_driver = driver
            events.append(
                self._event(
                    event,
                    RaceEventType.PROVISIONAL_POLE,
                    driver=driver,
                    payload={"previous_driver": previous_pole, "session_phase": state.phase},
                )
            )
        remaining = self._float(event.payload.get("time_remaining_seconds"))
        if (
            cutoff is not None
            and remaining is not None
            and remaining <= 120
            and position >= max(1, cutoff - 1)
            and self._risk_allowed(state, driver, event.event_time)
        ):
            state.risk_cooldowns[driver] = event.event_time
            events.append(
                self._event(
                    event,
                    RaceEventType.ELIMINATION_RISK,
                    driver=driver,
                    payload={
                        "position": position,
                        "cutoff_position": cutoff,
                        "time_remaining_seconds": remaining,
                        "session_phase": state.phase,
                    },
                )
            )
        return events

    def _lap_events(
        self,
        event: NormalizedRaceEvent,
        state: QualifyingState,
    ) -> list[NormalizedRaceEvent]:
        driver = event.driver_numbers[0]
        duration = self._float(event.payload.get("lap_duration"))
        if duration is None or duration <= 0:
            return []
        previous = state.best_laps.get(driver)
        if previous is not None and duration >= previous:
            return []
        state.best_laps[driver] = duration
        events = [
            self._event(
                event,
                RaceEventType.PERSONAL_BEST,
                driver=driver,
                payload={"lap_duration": duration, "previous_best": previous},
            )
        ]
        if state.session_best is None or duration < state.session_best:
            state.session_best = duration
            events.append(
                self._event(
                    event,
                    RaceEventType.FASTEST_LAP,
                    driver=driver,
                    payload={"lap_duration": duration, "session_phase": state.phase},
                )
            )
        return events

    def _initial_state(self, session_key: str, race_state: RaceState) -> QualifyingState:
        positions = {
            driver.driver_number: driver.position
            for driver in race_state.drivers.values()
            if driver.driver_number is not None and driver.position is not None
        }
        phase = str(race_state.current_phase or "").upper() or None
        pole = next((driver for driver, position in positions.items() if position == 1), None)
        return QualifyingState(
            session_key=session_key,
            phase=phase,
            field_size=len(positions),
            cutoff_position=qualifying_cutoff(len(positions), phase),
            positions=positions,
            provisional_pole_driver=pole,
        )

    def _risk_allowed(self, state: QualifyingState, driver: int, observed_at: object) -> bool:
        if not hasattr(observed_at, "__sub__"):
            return False
        previous = state.risk_cooldowns.get(driver)
        if previous is None:
            return True
        return (observed_at - previous).total_seconds() >= self.cooldown_seconds  # type: ignore[operator]

    @staticmethod
    def _event(
        source: NormalizedRaceEvent,
        event_type: RaceEventType,
        *,
        driver: int | None = None,
        payload: dict[str, object],
    ) -> NormalizedRaceEvent:
        primary = driver
        if primary is None and source.driver_numbers:
            primary = source.driver_numbers[0]
        return NormalizedRaceEvent(
            meeting_id=source.meeting_id,
            session_id=source.session_id,
            session_key=source.session_key,
            source="apexarena",
            event_origin=EventOrigin.DERIVED,
            event_time=source.event_time,
            received_at=source.received_at,
            event_type=event_type,
            primary_driver_number=primary,
            lap_number=source.lap_number,
            importance=0.55,
            importance_level=EventImportance.NORMAL,
            confidence=0.95,
            confidence_level=EventConfidence.HIGH,
            derivation=EventDerivation(
                algorithm="qualifying_intelligence_v1",
                evidence=[
                    DerivationEvidence(
                        kind="qualifying_state_transition",
                        observed_at=source.event_time,
                        event_id=source.id,
                    )
                ],
            ),
            payload=payload,
            dedup_key=(
                f"qualifying:{event_type.value.lower()}:{source.session_key}:"
                f"{primary or 0}:{source.sequence_number}"
            ),
            is_replay=source.is_replay,
        )

    @staticmethod
    def _positive_int(value: object) -> int | None:
        try:
            parsed = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    @staticmethod
    def _float(value: object) -> float | None:
        try:
            return float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
