# SPDX-License-Identifier: AGPL-3.0-only
"""Private bounded factual projection. Never nested in the public RaceState."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.control import ControlProjection
from app.domain.models import EventOrigin, NormalizedRaceEvent, RaceEventType
from app.domain.strategy import FactReference, SessionHistory
from app.services.control_state import apply_control, lap_control_context
from app.services.race_history import _apply_history_owned as apply_history

HISTORY_TYPES = frozenset(
    {
        RaceEventType.LAP_COMPLETED,
        RaceEventType.LAP_DELETED,
        RaceEventType.PIT_STOP,
        RaceEventType.STINT_UPDATE,
        RaceEventType.WEATHER_UPDATE,
        RaceEventType.WEATHER_CHANGE,
    }
)


class SessionFactualContext(BaseModel):
    session_key: str
    history: SessionHistory = Field(
        default_factory=lambda data: SessionHistory(session_key=data["session_key"])
    )
    control: ControlProjection = Field(
        default_factory=lambda data: ControlProjection(session_key=data["session_key"])
    )
    relevant_sequence: int = 0
    relevant_event_id: UUID | None = None
    relevant_count: int = 0
    analysis_time: datetime | None = None

    @staticmethod
    def is_relevant_source(event: NormalizedRaceEvent) -> bool:
        return event.event_origin is EventOrigin.SOURCE_FACT and (
            bool(event.payload.get("control"))
            or event.event_type in HISTORY_TYPES
            or event.event_type in {RaceEventType.SESSION_FINISH, RaceEventType.SESSION_END}
        )

    def advance_owned(self, event: NormalizedRaceEvent) -> set[str]:
        """Mutate one exclusively owned candidate in stored append order."""
        if event.session_key != self.session_key:
            raise ValueError("Factual context session mismatch")
        if event.event_origin is not EventOrigin.SOURCE_FACT:
            return set()
        self.analysis_time = max(self.analysis_time or event.event_time, event.event_time)
        is_control = bool(event.payload.get("control")) or event.event_type in {
            RaceEventType.SESSION_FINISH,
            RaceEventType.SESSION_END,
        }
        if event.sequence_number <= self.relevant_sequence or not self.is_relevant_source(event):
            return set()
        self.relevant_sequence = event.sequence_number
        self.relevant_event_id = event.id
        self.relevant_count += 1
        if is_control:
            self.control = apply_control(self.control, event)
        authority_before = (
            self.history.lap_history_truncated,
            self.history.driver_history_truncated,
            self.history.unresolved_deletions,
        )
        if event.event_type in HISTORY_TYPES:
            apply_history(self.history, event)
        self.history.sequence = self.relevant_sequence
        changed = (
            set(self.history.drivers)
            if is_control
            else {
                str(
                    event.primary_driver_number
                    or (event.driver_numbers[0] if event.driver_numbers else 0)
                )
            }
        )
        authority_after = (
            self.history.lap_history_truncated,
            self.history.driver_history_truncated,
            self.history.unresolved_deletions,
        )
        dirty = set() if is_control else set(changed)
        if authority_after != authority_before:
            changed.update(self.history.drivers)
            dirty.update(self.history.drivers)
        addressed_lap = event.lap_number or event.payload.get("lap_number")
        for key in changed:
            driver = self.history.drivers.get(key)
            if driver is None:
                continue
            for lap in driver.laps:
                if not is_control and str(lap.lap_number) != str(addressed_lap):
                    continue
                context = (
                    lap_control_context(self.control, lap.interval_start, lap.interval_end)
                    if lap.interval_start is not None and lap.interval_end is not None
                    else "unknown"
                )
                exclusions = [
                    reason
                    for reason in lap.exclusions
                    if reason not in {"neutralized", "control_unknown"}
                ]
                if context != "green":
                    exclusions.append(
                        "neutralized" if context == "neutralized" else "control_unknown"
                    )
                rows = [
                    row
                    for row in self.control.history
                    if lap.interval_end is not None and row.observed_at <= lap.interval_end
                ]
                evidence = rows[-3:]
                evidence_changed = len(evidence) != len(lap.control_evidence) or any(
                    (old.event_id, old.sequence, old.observed_at, old.source)
                    != (new.event_id, new.sequence, new.observed_at, new.source)
                    for old, new in zip(lap.control_evidence, evidence, strict=False)
                )
                if exclusions != lap.exclusions or evidence_changed:
                    dirty.add(key)
                    lap.exclusions = exclusions
                    if evidence_changed:
                        lap.control_evidence = [
                            FactReference(**row.model_dump(exclude={"semantics"}))
                            for row in evidence
                        ]
        return dirty
