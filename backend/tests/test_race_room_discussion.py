# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.domain.models import RaceEventType
from app.domain.rooms import Confidence, EvidenceStatus, MessageEvidence, MessageType, RoomMessage
from app.services.discussion import (
    DeterministicRoomGenerator,
    GeneratedRoomMessage,
    GroundedClaim,
    GroundingContext,
    GroundingValidator,
    RaceRoomDiscussionEngine,
)
from app.services.discussion_triggers import DiscussionTriggerEvaluator
from app.services.room_agents import DEFAULT_ROOM_AGENTS
from tests.fixtures.race_room_events import race_room_event, ten_lap_fixture


class FakeRoomRepository:
    def __init__(self) -> None:
        self.room = SimpleNamespace(id=uuid4(), slug="fixture-room")
        self.messages: list[RoomMessage] = []
        self.evidence: dict[str, list[MessageEvidence]] = {}

    async def get_room_by_session(self, session_key: str):
        return self.room if session_key == "test-race-room" else None

    async def insert_message(
        self, message: RoomMessage, evidence: list[MessageEvidence]
    ) -> tuple[RoomMessage, bool]:
        stored = message.model_copy(update={"sequence": len(self.messages) + 1})
        self.messages.append(stored)
        self.evidence[str(stored.id)] = evidence
        return stored, True


def test_roster_has_five_distinct_enabled_specialists() -> None:
    assert [agent.display_name for agent in DEFAULT_ROOM_AGENTS] == [
        "Mira Vale",
        "Theo Voss",
        "Lena Cross",
        "Arjun Reyes",
        "Nova",
    ]
    assert len({topic for agent in DEFAULT_ROOM_AGENTS for topic in agent.supported_topics}) >= 8
    assert all(agent.active and agent.personality_rules for agent in DEFAULT_ROOM_AGENTS)


def test_trigger_evaluator_ignores_noise_deduplicates_and_respects_cooldown() -> None:
    evaluator = DiscussionTriggerEvaluator(topic_cooldown_seconds=60)
    noise = race_room_event(RaceEventType.UNKNOWN_PROVIDER_EVENT)
    meaningful = race_room_event(RaceEventType.PIT_STOP, sequence=2)
    same_topic = race_room_event(RaceEventType.PIT_STOP, sequence=3)
    assert evaluator.evaluate(noise) is None
    assert evaluator.evaluate(meaningful) is not None
    assert evaluator.evaluate(meaningful) is None
    assert evaluator.evaluate(same_topic) is None


def test_fixture_covers_ten_laps_and_critical_moments() -> None:
    events = ten_lap_fixture()
    assert max(event.lap_number or 0 for event in events) >= 10
    assert len({driver for event in events for driver in event.driver_numbers}) >= 3
    critical = {RaceEventType.SAFETY_CAR, RaceEventType.SESSION_FINISH}
    assert critical <= {event.event_type for event in events}
    assert RaceEventType.OVERTAKE in {event.event_type for event in events}


def test_deterministic_pit_message_only_claims_available_fields() -> None:
    event = race_room_event(payload={"pit_duration": 2.41})
    trigger = DiscussionTriggerEvaluator(topic_cooldown_seconds=0).evaluate(event)
    assert trigger is not None
    generated = DeterministicRoomGenerator().generate(event, trigger, "mira-vale")
    assert "2.41 seconds" in generated.content
    assert "remain uncertain" in generated.content
    assert generated.evidence_status is EvidenceStatus.GROUNDED
    assert generated.confidence is Confidence.HIGH


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


def test_grounding_validator_rejects_an_unsupported_evidence_key() -> None:
    generated = GeneratedRoomMessage(
        message_type=MessageType.ANALYSIS,
        content="Driver 4 has an unsupported claim.",
        confidence=Confidence.MEDIUM,
        evidence_status=EvidenceStatus.PARTIAL,
        claims=[GroundedClaim(claim="Unsupported", evidence_keys=["invented_metric"])],
    )
    context = GroundingContext(
        evidence={"event_type": "PIT_STOP", "driver_numbers": [4]},
        data_quality="partial",
    )
    assert not GroundingValidator().validate(generated, context)


@pytest.mark.asyncio
async def test_discussion_chain_is_bounded_and_preserves_reply_relationships() -> None:
    repository = FakeRoomRepository()
    engine = RaceRoomDiscussionEngine(
        repository,  # type: ignore[arg-type]
        DiscussionTriggerEvaluator(topic_cooldown_seconds=0, agent_cooldown_seconds=0),
    )
    event = race_room_event(
        RaceEventType.SAFETY_CAR,
        payload={"message": "Safety car deployed"},
    )

    await engine.consume(event)

    assert len(repository.messages) == 3
    assert repository.messages[1].reply_to_message_id == repository.messages[0].id
    assert repository.messages[2].message_type is MessageType.SUMMARY
    assert all(repository.evidence[str(message.id)] for message in repository.messages)


@pytest.mark.asyncio
async def test_position_change_produces_a_grounded_disagreement() -> None:
    repository = FakeRoomRepository()
    engine = RaceRoomDiscussionEngine(
        repository,  # type: ignore[arg-type]
        DiscussionTriggerEvaluator(topic_cooldown_seconds=0, agent_cooldown_seconds=0),
    )
    await engine.consume(
        race_room_event(
            RaceEventType.POSITION_CHANGE,
            payload={"previous_position": 7, "position": 6},
        )
    )

    assert [message.agent_id for message in repository.messages] == ["lena-cross", "theo-voss"]
    assert repository.messages[1].message_type is MessageType.DISAGREEMENT
