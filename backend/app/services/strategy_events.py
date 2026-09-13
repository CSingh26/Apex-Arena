# SPDX-License-Identifier: AGPL-3.0-only
"""One strict strategy boundary shared by importance, topics and grounding."""

from pydantic import ValidationError

from app.domain.models import (
    EventConfidence,
    EventDerivation,
    EventOrigin,
    NormalizedRaceEvent,
    RaceEventType,
)
from app.domain.strategy_situations import EVENT_BYTES, StrategyTransition, encoded


def validated_strategy(event):
    if (
        event.event_type != RaceEventType.STRATEGY_SITUATION
        or event.event_origin != EventOrigin.DERIVED
        or event.derivation is None
        or event.derivation.algorithm != "strategy-v1"
    ):
        return None
    try:
        transition = StrategyTransition.model_validate(event.payload)
        item = transition.situation
        roles = {r.role for r in transition.evidence.values()}
        if (
            item.session_key != event.session_key
            or item.sequence > event.sequence_number
            or len(encoded(event)) > EVENT_BYTES
            or item.availability == "unavailable"
        ):
            return None
        if item.status == "withdrawn":
            return (
                transition
                if item.superseded_revision_id is not None and item.transition == "withdrawn"
                else None
            )
        p = item.payload
        valid = {
            "stint_divergence": len(p.tyres) == 2 and "stint" in roles,
            "relative_pace": p.pace is not None and {"lap", "stint", "control"} <= roles,
            "pit_window": p.pit_window is not None
            and {"lap", "pit", "interval", "control"} <= roles,
            "undercut_condition": p.pace is not None
            and p.pit_window is not None
            and p.required_gain_seconds is not None
            and {"lap", "pit", "interval", "position", "stint", "control"} <= roles,
            "overcut_condition": p.pace is not None
            and p.pit_anchor is not None
            and p.clean_laps_since_pit is not None
            and {"lap", "pit", "interval", "position", "stint", "control"} <= roles,
            "neutralized_pit_context": p.neutralization
            in {
                "safety_car",
                "safety_car_ending",
                "virtual_safety_car",
                "virtual_safety_car_ending",
            }
            and "control" in roles,
            "extra_stop_consequence": p.remaining_laps is not None
            and p.required_average_gain_seconds is not None
            and {"distance", "pit", "lap"} <= roles,
            "weather_change": len(transition.evidence) >= 2 and roles == {"weather"},
        }
        return transition if valid.get(item.kind.value, False) else None
    except (ValidationError, ValueError, TypeError):
        return None


def strategy_event(source, transition):
    item = transition.situation
    event = NormalizedRaceEvent(
        id=item.revision_id,
        session_key=source.session_key,
        meeting_id=source.meeting_id,
        session_id=source.session_id,
        source="apexarena",
        event_origin=EventOrigin.DERIVED,
        event_type=RaceEventType.STRATEGY_SITUATION,
        event_time=item.analysis_time,
        received_at=source.received_at,
        sequence_number=source.sequence_number,
        driver_numbers=item.participants,
        confidence=0.4,
        confidence_level=EventConfidence.LOW,
        derivation=EventDerivation(algorithm="strategy-v1"),
        payload=transition.model_dump(mode="json"),
        dedup_key=f"strategy:{item.revision_id}",
        is_replay=source.is_replay,
    )
    return event if validated_strategy(event) else None
