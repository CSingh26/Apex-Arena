# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.intelligence import BattleIntensity, BattleState, BattleStatus, BattleTrend
from app.domain.models import (
    DerivationEvidence,
    EventConfidence,
    EventDerivation,
    EventImportance,
    EventOrigin,
    NormalizedRaceEvent,
    RaceEventType,
)
from app.services.agent_grounding import AgentEligibility, AgentEventEnvelope
from app.services.race_state import DriverRaceState, RaceState

NOW = datetime(2026, 7, 19, 13, 38, tzinfo=UTC)


def race_event(
    event_type: RaceEventType,
    *,
    origin: EventOrigin = EventOrigin.SOURCE_FACT,
    importance: EventImportance = EventImportance.LOW,
    confidence: EventConfidence = EventConfidence.HIGH,
) -> NormalizedRaceEvent:
    return NormalizedRaceEvent(
        session_key="11334",
        source="apexarena" if origin is EventOrigin.DERIVED else "openf1",
        event_origin=origin,
        event_time=NOW,
        received_at=NOW,
        sequence_number=80,
        event_type=event_type,
        primary_driver_number=4,
        secondary_driver_number=16,
        position_before=5,
        position_after=4,
        interval_seconds=0.72,
        lap_number=38,
        importance=0.82,
        importance_level=importance,
        confidence=0.9,
        confidence_level=confidence,
        dedup_key=f"grounding:{event_type.value}",
    )


@pytest.mark.parametrize(
    "event_type",
    [
        RaceEventType.POSITION_SAMPLE,
        RaceEventType.INTERVAL_SAMPLE,
        RaceEventType.LOCATION_SAMPLE,
        RaceEventType.CAR_DATA_SAMPLE,
        RaceEventType.DRIVER_UPDATE,
    ],
)
def test_agent_eligibility_rejects_raw_samples(event_type: RaceEventType) -> None:
    assert AgentEligibility().evaluate(race_event(event_type)) is False


def test_agent_eligibility_accepts_important_events_and_rejects_low_confidence() -> None:
    eligibility = AgentEligibility()
    assert not eligibility.evaluate(
        race_event(RaceEventType.LAP_COMPLETED, importance=EventImportance.LOW)
    )
    assert eligibility.evaluate(
        race_event(RaceEventType.SAFETY_CAR, importance=EventImportance.MAJOR)
    )
    assert eligibility.evaluate(
        race_event(
            RaceEventType.OVERTAKE,
            origin=EventOrigin.DERIVED,
            importance=EventImportance.IMPORTANT,
        )
    )
    assert eligibility.evaluate(
        race_event(RaceEventType.RED_FLAG, importance=EventImportance.CRITICAL)
    )
    assert not eligibility.evaluate(
        race_event(
            RaceEventType.OVERTAKE,
            origin=EventOrigin.DERIVED,
            importance=EventImportance.IMPORTANT,
            confidence=EventConfidence.LOW,
        )
    )


def test_agent_envelope_contains_supported_facts_without_raw_telemetry_or_location() -> None:
    battle = BattleState(
        id="11334:16:4",
        session_key="11334",
        lead_driver_number=16,
        chasing_driver_number=4,
        lead_position=4,
        chasing_position=5,
        interval_seconds=0.72,
        closest_interval_seconds=0.61,
        interval_history=[0.9, 0.8, 0.72],
        started_at=datetime(2026, 7, 19, 13, 35, tzinfo=UTC),
        last_updated_at=NOW,
        trend=BattleTrend.CLOSING,
        intensity=BattleIntensity.INTENSE,
        status=BattleStatus.INTENSE,
        within_one_second=True,
    )
    state = RaceState(
        session_key="11334",
        session_type="RACE",
        current_phase="RACE",
        current_lap=38,
        current_battles=[battle],
        drivers={
            "4": DriverRaceState(
                driver_number=4,
                full_name="Lando Norris",
                position=4,
                status="RUNNING",
                stint={"compound": "MEDIUM", "lap_start": 31},
                telemetry={"speed": 320, "throttle": 100},
                location={"x": 123.0, "y": 456.0},
            ),
            "16": DriverRaceState(
                driver_number=16,
                full_name="Charles Leclerc",
                position=5,
                status="RUNNING",
                stint={"compound": "HARD", "lap_start": 20},
            ),
        },
    )
    event = race_event(
        RaceEventType.OVERTAKE,
        origin=EventOrigin.DERIVED,
        importance=EventImportance.IMPORTANT,
    ).model_copy(
        update={
            "derivation": EventDerivation(
                algorithm="overtake_detector_v1",
                evidence=[
                    DerivationEvidence(
                        kind="persistent_order_reversal",
                        observed_at=NOW,
                        value=True,
                    )
                ],
            )
        }
    )

    envelope = AgentEventEnvelope.from_event(event, state)
    evidence = envelope.as_evidence()

    assert evidence["session_key"] == "11334"
    assert evidence["session_type"] == "RACE"
    assert evidence["driver_numbers"] == [4, 16]
    assert evidence["position"] == 4
    assert evidence["previous_position"] == 5
    assert evidence["interval_seconds"] == 0.72
    assert evidence["battle"]["trend"] == "CLOSING"
    assert evidence["relevant_driver_state"]["4"]["compound"] == "MEDIUM"
    assert evidence["derivation_evidence"][0]["kind"] == "persistent_order_reversal"
    assert "telemetry" not in str(evidence)
    assert "location" not in str(evidence)
