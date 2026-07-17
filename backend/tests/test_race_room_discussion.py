# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from app.domain.models import RaceEventType
from app.domain.rooms import Confidence, EvidenceStatus, MessageEvidence, RoomMessage
from app.services.discussion import DeterministicRoomGenerator, GroundingValidator
from app.services.discussion_triggers import DiscussionTriggerEvaluator
from app.services.room_agents import DEFAULT_ROOM_AGENTS
from tests.fixtures.race_room_events import race_room_event, ten_lap_fixture


def test_roster_has_five_distinct_enabled_specialists() -> None:
    assert [agent.display_name for agent in DEFAULT_ROOM_AGENTS] == [
        "Mira Vale", "Theo Voss", "Lena Cross", "Arjun Reyes", "Nova"
    ]
    assert len({topic for agent in DEFAULT_ROOM_AGENTS for topic in agent.supported_topics}) >= 8
    assert all(agent.active and agent.personality_rules for agent in DEFAULT_ROOM_AGENTS)


def test_trigger_evaluator_ignores_noise_deduplicates_and_respects_cooldown() -> None:
    evaluator = DiscussionTriggerEvaluator(topic_cooldown_seconds=60)
    noise = race_room_event(RaceEventType.WEATHER_UPDATE)
    meaningful = race_room_event(RaceEventType.PIT_STOP, sequence=2)
    same_topic = race_room_event(RaceEventType.PIT_STOP, sequence=3)
    assert evaluator.evaluate(noise) is None
    assert evaluator.evaluate(meaningful) is not None
    assert evaluator.evaluate(meaningful) is None
    assert evaluator.evaluate(same_topic) is None


def test_fixture_covers_ten_laps_and_critical_moments() -> None:
    events = ten_lap_fixture()
    assert [event.lap_number for event in events] == list(range(1, 11))
    critical = {RaceEventType.SAFETY_CAR, RaceEventType.SESSION_FINISH}
    assert critical <= {event.event_type for event in events}


def test_deterministic_pit_message_only_claims_available_fields() -> None:
    event = race_room_event(payload={"pit_duration": 2.41})
    trigger = DiscussionTriggerEvaluator(topic_cooldown_seconds=0).evaluate(event)
    assert trigger is not None
    content, status, confidence = DeterministicRoomGenerator().generate(event, trigger, "mira-vale")
    assert "2.41 seconds" in content
    assert "Tyre compound data is unavailable" in content
    assert status is EvidenceStatus.GROUNDED
    assert confidence is Confidence.HIGH


def test_grounding_validator_rejects_invented_radio_and_missing_evidence() -> None:
    event = race_room_event()
    message = RoomMessage(
        room_id=event.id,
        agent_id="nova",
        sequence=1,
        topic="summary",
        message_type="analysis",
        content="Team radio says box now",
        evidence_status="grounded",
    )
    validator = GroundingValidator()
    assert not validator.validate(message, [])
    message.content = "A pit stop was observed."
    assert not validator.validate(message, [])
    evidence = [
            MessageEvidence(
                message_id=message.id,
                evidence_key="event_type",
            evidence_type="normalized_event",
            source_provider="fixture",
            source_reference=str(event.id),
        )
    ]
    assert validator.validate(message, evidence)
