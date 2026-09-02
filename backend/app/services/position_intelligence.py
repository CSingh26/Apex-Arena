# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime

from app.domain.intelligence import PositionChange, PositionChangeCause, PositionState
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

RACE_LIKE_SESSIONS = {"RACE", "SPRINT"}
RETIRED_STATUSES = {"RETIRED", "STOPPED", "DNF", "DNS"}
logger = logging.getLogger(__name__)


class PositionTracker:
    """Confirm coherent classification changes without inferring their race meaning."""

    def __init__(self) -> None:
        self._states: dict[str, dict[int, PositionState]] = defaultdict(dict)

    def reset_session(self, session_key: str) -> None:
        self._states.pop(session_key, None)

    def state_count(self, session_key: str) -> int:
        return len(self._states.get(session_key, {}))

    def apply(self, event: NormalizedRaceEvent, race_state: RaceState) -> list[PositionChange]:
        if event.event_type is not RaceEventType.POSITION_SAMPLE or not event.driver_numbers:
            return []
        position = self._positive_int(event.payload.get("position"))
        if position is None:
            return []
        driver_number = event.driver_numbers[0]
        session = self._states[event.session_key]
        current = session.get(driver_number)
        if current is not None and (
            event.event_time < current.last_observed_at
            or event.sequence_number <= current.last_sequence
        ):
            return []

        context = dict(event.payload)
        status = str(context.get("status") or "RUNNING").upper()
        in_pit = bool(context.get("in_pit") or context.get("pit_state") in {"IN", "PIT"})
        if current is None:
            session[driver_number] = PositionState(
                driver_number=driver_number,
                observed_position=position,
                confirmed_position=position,
                start_position=position,
                status=status,
                in_pit=in_pit,
                last_observed_at=event.event_time,
                last_sequence=event.sequence_number,
                context=context,
            )
            return []

        current.observed_position = position
        current.status = status
        current.in_pit = in_pit
        current.last_observed_at = event.event_time
        current.last_sequence = event.sequence_number
        current.context = context

        positions = [driver.observed_position for driver in session.values()]
        if len(positions) != len(set(positions)):
            return []
        changed = [
            driver
            for driver in session.values()
            if driver.observed_position != driver.confirmed_position
        ]
        if len(changed) < 2:
            return []

        cause = self._classify(changed, race_state)
        batch_key = self._batch_key(event.session_key, event.event_time, changed)
        changed_numbers = [driver.driver_number for driver in changed]
        changes = [
            PositionChange(
                session_key=event.session_key,
                driver_number=driver.driver_number,
                related_driver_numbers=[
                    number for number in changed_numbers if number != driver.driver_number
                ],
                position_before=driver.confirmed_position,
                position_after=driver.observed_position,
                cause=cause,
                observed_at=event.event_time,
                source_sequence=event.sequence_number,
                batch_key=batch_key,
            )
            for driver in sorted(changed, key=lambda item: item.observed_position)
        ]
        logger.debug(
            "position_change_classified session=%s drivers=%s cause=%s batch=%s",
            event.session_key,
            changed_numbers,
            cause.value,
            batch_key,
        )
        for driver in changed:
            driver.previous_confirmed_position = driver.confirmed_position
            driver.confirmed_position = driver.observed_position
        return changes

    def events_for(
        self,
        changes: list[PositionChange],
        *,
        source_event: NormalizedRaceEvent,
    ) -> list[NormalizedRaceEvent]:
        events: list[NormalizedRaceEvent] = []
        for change in changes:
            direction = (
                RaceEventType.POSITION_GAIN
                if change.position_delta > 0
                else RaceEventType.POSITION_LOSS
            )
            for event_type in (RaceEventType.POSITION_CHANGE, direction):
                events.append(self._derived_event(change, event_type, source_event))
        return events

    @staticmethod
    def _classify(changed: list[PositionState], race_state: RaceState) -> PositionChangeCause:
        session_type = str(race_state.session_type or "").upper()
        if session_type not in RACE_LIKE_SESSIONS:
            return PositionChangeCause.PENALTY_OR_CLASSIFICATION
        if any(driver.in_pit for driver in changed):
            return PositionChangeCause.PIT_CYCLE
        if any(driver.status in RETIRED_STATUSES for driver in changed):
            return PositionChangeCause.RETIREMENT_INHERITANCE
        if any(bool(driver.context.get("lapped")) for driver in changed):
            return PositionChangeCause.LAPPED_ORDERING
        if race_state.race_control_state.get("event_type") == RaceEventType.PENALTY.value or any(
            bool(driver.context.get("penalty")) for driver in changed
        ):
            return PositionChangeCause.PENALTY_OR_CLASSIFICATION
        before = {driver.confirmed_position for driver in changed}
        after = {driver.observed_position for driver in changed}
        if len(changed) == 2 and before == after:
            return PositionChangeCause.ON_TRACK_CANDIDATE
        if len(changed) > 2:
            return PositionChangeCause.TIMING_CORRECTION
        return PositionChangeCause.UNKNOWN

    @staticmethod
    def _batch_key(session_key: str, observed_at: datetime, changed: list[PositionState]) -> str:
        drivers = "-".join(
            str(item.driver_number) for item in sorted(changed, key=lambda item: item.driver_number)
        )
        return f"{session_key}:{observed_at.isoformat()}:{drivers}"

    @staticmethod
    def _derived_event(
        change: PositionChange,
        event_type: RaceEventType,
        source_event: NormalizedRaceEvent,
    ) -> NormalizedRaceEvent:
        related = change.related_driver_numbers[0] if change.related_driver_numbers else None
        return NormalizedRaceEvent(
            meeting_id=source_event.meeting_id,
            session_id=source_event.session_id,
            session_key=source_event.session_key,
            source="apexarena",
            event_origin=EventOrigin.DERIVED,
            event_time=change.observed_at,
            received_at=source_event.received_at,
            event_type=event_type,
            primary_driver_number=change.driver_number,
            secondary_driver_number=related,
            position_before=change.position_before,
            position_after=change.position_after,
            lap_number=source_event.lap_number,
            importance=0.4,
            importance_level=EventImportance.NORMAL,
            confidence=0.9,
            confidence_level=EventConfidence.HIGH,
            derivation=EventDerivation(
                algorithm="position_change_classifier_v1",
                version=1,
                evidence=[
                    DerivationEvidence(
                        kind="coherent_order_update",
                        observed_at=change.observed_at,
                        event_id=source_event.id,
                        value=change.batch_key,
                    )
                ],
                exclusions_checked=["duplicate_order", "stale_update"],
            ),
            payload={
                "cause": change.cause.value,
                "position_delta": change.position_delta,
                "batch_key": change.batch_key,
            },
            dedup_key=(
                f"{event_type.value.lower()}:{source_event.session_key}:"
                f"{change.driver_number}:{change.source_sequence}"
            ),
            is_replay=source_event.is_replay,
        )

    @staticmethod
    def _positive_int(value: object) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None
