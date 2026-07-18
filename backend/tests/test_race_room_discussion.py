# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.domain.models import RaceEventType
from app.domain.rooms import (
    Confidence,
    EvidenceStatus,
    MessageEvidence,
    MessageTopic,
    MessageType,
    RoomMessage,
)
from app.services.discussion import (
    DeterministicRoomGenerator,
    GeneratedRoomMessage,
    GroundedClaim,
    GroundingContext,
    GroundingContextBuilder,
    GroundingValidator,
    RaceRoomDiscussionEngine,
)
from app.services.discussion_triggers import DiscussionTriggerEvaluator, TriggerPriority
from app.services.race_state import DriverRaceState, RaceState
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


def test_trigger_evaluator_only_emits_meaningful_laps_and_promotes_pace_trends() -> None:
    evaluator = DiscussionTriggerEvaluator(
        topic_cooldown_seconds=0,
        agent_cooldown_seconds=0,
    )

    assert evaluator.evaluate(race_room_event(RaceEventType.LAP_COMPLETED, lap=2)) is None
    lap_ten = evaluator.evaluate(race_room_event(RaceEventType.LAP_COMPLETED, sequence=2, lap=10))
    pace_trend = evaluator.evaluate(
        race_room_event(
            RaceEventType.LAP_COMPLETED,
            sequence=3,
            lap=3,
            payload={"pace_trend_seconds": -0.24},
        )
    )

    assert lap_ten is not None
    assert lap_ten.priority is TriggerPriority.LOW
    assert pace_trend is not None
    assert pace_trend.priority is TriggerPriority.HIGH
    assert pace_trend.needs_reply is True


def test_critical_triggers_bypass_topic_agent_cooldowns_and_room_throttle() -> None:
    evaluator = DiscussionTriggerEvaluator(
        topic_cooldown_seconds=600,
        agent_cooldown_seconds=600,
        room_max_triggers_per_minute=1,
    )

    first = evaluator.evaluate(race_room_event(RaceEventType.SAFETY_CAR))
    second = evaluator.evaluate(race_room_event(RaceEventType.VIRTUAL_SAFETY_CAR, sequence=2))

    assert first is not None and second is not None
    assert first.priority is TriggerPriority.CRITICAL
    assert second.priority is TriggerPriority.CRITICAL
    assert second.needs_host_summary is True


def test_trigger_throttle_is_scoped_to_session_and_reset_allows_replay() -> None:
    evaluator = DiscussionTriggerEvaluator(
        topic_cooldown_seconds=0,
        agent_cooldown_seconds=0,
        room_max_triggers_per_minute=1,
    )
    first = race_room_event(RaceEventType.PIT_STOP)
    throttled = race_room_event(RaceEventType.POSITION_CHANGE, sequence=2)
    other_session = throttled.model_copy(
        update={"session_key": "other-room", "dedup_key": "other-room:position"}
    )

    assert evaluator.evaluate(first) is not None
    assert evaluator.evaluate(throttled) is None
    assert evaluator.evaluate(other_session) is not None

    evaluator.reset_session("test-race-room")
    assert evaluator.evaluate(first) is not None


def test_resetting_one_session_preserves_other_sessions_dedup_history() -> None:
    evaluator = DiscussionTriggerEvaluator(
        topic_cooldown_seconds=0,
        agent_cooldown_seconds=0,
    )
    first_room = race_room_event(RaceEventType.PIT_STOP)
    other_room = race_room_event(RaceEventType.POSITION_CHANGE, sequence=2).model_copy(
        update={"session_key": "other-room", "dedup_key": "other-room:position:2"}
    )

    assert evaluator.evaluate(first_room) is not None
    assert evaluator.evaluate(other_room) is not None

    evaluator.reset_session(first_room.session_key)

    assert evaluator.evaluate(first_room) is not None
    assert evaluator.evaluate(other_room) is None


def test_trigger_dedup_memory_is_bounded() -> None:
    evaluator = DiscussionTriggerEvaluator(
        topic_cooldown_seconds=0,
        agent_cooldown_seconds=0,
        dedup_capacity=1,
    )
    first = race_room_event(RaceEventType.PIT_STOP)
    second = race_room_event(RaceEventType.POSITION_CHANGE, sequence=2)

    assert evaluator.evaluate(first) is not None
    assert evaluator.evaluate(second) is not None
    assert evaluator.evaluate(first) is not None


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
    assert "My call" in generated.content
    assert "do not celebrate" in generated.content
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


@pytest.mark.parametrize(
    ("content", "confidence", "claims"),
    [
        (
            "Driver 44 made a pit stop.",
            Confidence.MEDIUM,
            [GroundedClaim(claim="A stop occurred", evidence_keys=["event_type"])],
        ),
        (
            "Driver 4 made a pit stop.",
            Confidence.HIGH,
            [GroundedClaim(claim="A stop occurred", evidence_keys=["event_type"])],
        ),
        ("Driver 4 made a pit stop.", Confidence.MEDIUM, []),
    ],
)
def test_grounding_validator_rejects_unknown_driver_high_incomplete_or_no_claims(
    content: str,
    confidence: Confidence,
    claims: list[GroundedClaim],
) -> None:
    message = GeneratedRoomMessage(
        message_type=MessageType.ANALYSIS,
        content=content,
        confidence=confidence,
        evidence_status=EvidenceStatus.PARTIAL,
        claims=claims,
    )
    context = GroundingContext(
        evidence={"event_type": "PIT_STOP", "driver_numbers": [4]},
        data_quality="incomplete",
    )

    assert not GroundingValidator().validate(message, context)


def test_grounding_context_only_includes_relevant_driver_state() -> None:
    event = race_room_event(RaceEventType.PIT_STOP, payload={"data_quality": "complete"})
    state = RaceState(
        session_key=event.session_key,
        status="started",
        current_lap=12,
        drivers={
            "4": DriverRaceState(position=2),
            "44": DriverRaceState(position=7),
        },
    )

    context = GroundingContextBuilder().build(event, state)

    assert context.data_quality == "complete"
    assert context.evidence["race_status"] == "started"
    assert context.evidence["race_current_lap"] == 12
    assert set(context.evidence["relevant_driver_state"]) == {"4"}


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


@pytest.mark.asyncio
async def test_qualifying_reply_challenges_the_easy_conclusion() -> None:
    repository = FakeRoomRepository()
    engine = RaceRoomDiscussionEngine(
        repository,  # type: ignore[arg-type]
        DiscussionTriggerEvaluator(topic_cooldown_seconds=0, agent_cooldown_seconds=0),
    )
    event = race_room_event(
        RaceEventType.LAP_COMPLETED,
        payload={
            "normalized_session_type": "QUALIFYING",
            "session_phase": "Q2",
            "lap_duration": 88.42,
            "is_personal_best": True,
        },
    )

    await engine.consume(event)

    assert [message.agent_id for message in repository.messages] == [
        "theo-voss",
        "lena-cross",
    ]
    assert repository.messages[1].message_type is MessageType.DISAGREEMENT
    assert "Quick lap, yes. Safe? Not yet." in repository.messages[1].content


@pytest.mark.asyncio
async def test_mira_pace_reply_is_classified_as_strategy_analysis() -> None:
    repository = FakeRoomRepository()
    engine = RaceRoomDiscussionEngine(
        repository,  # type: ignore[arg-type]
        DiscussionTriggerEvaluator(topic_cooldown_seconds=0, agent_cooldown_seconds=0),
    )

    await engine.consume(
        race_room_event(
            RaceEventType.LAP_COMPLETED,
            lap=9,
            payload={
                "pace_trend_seconds": -0.42,
                "representative_laps": [92.1, 91.8, 91.68],
            },
        )
    )

    assert [message.agent_id for message in repository.messages] == [
        "theo-voss",
        "mira-vale",
    ]
    assert repository.messages[1].topic is MessageTopic.STRATEGY
    assert "0.42-second pace gain" in repository.messages[1].content
    assert "speed can still become a trap" in repository.messages[1].content


def test_position_message_takes_a_grounded_stand_on_the_stat_line() -> None:
    event = race_room_event(
        RaceEventType.OVERTAKE,
        payload={"previous_position": 7, "position": 6},
    )
    trigger = DiscussionTriggerEvaluator(topic_cooldown_seconds=0).evaluate(event)
    assert trigger is not None

    generated = DeterministicRoomGenerator().generate(event, trigger, "lena-cross")

    assert "from P7 to P6" in generated.content
    assert "clean track-position win" in generated.content
    assert GroundingValidator().validate(generated, GroundingContextBuilder().build(event, None))


@pytest.mark.asyncio
async def test_message_publication_failure_does_not_rollback_grounded_messages() -> None:
    repository = FakeRoomRepository()

    async def failing_publisher(_: RoomMessage) -> None:
        raise ConnectionError("redis://private-host:6379")

    engine = RaceRoomDiscussionEngine(
        repository,  # type: ignore[arg-type]
        DiscussionTriggerEvaluator(topic_cooldown_seconds=0, agent_cooldown_seconds=0),
        publisher=failing_publisher,
    )

    await engine.consume(race_room_event(RaceEventType.PIT_STOP))

    assert len(repository.messages) == 2
    assert engine.metrics.generated_message_count == 2
    assert engine.metrics.deterministic_fallback_count == 2


@pytest.mark.asyncio
async def test_repeated_content_is_suppressed_and_counted_as_rejected() -> None:
    repository = FakeRoomRepository()
    engine = RaceRoomDiscussionEngine(
        repository,  # type: ignore[arg-type]
        DiscussionTriggerEvaluator(topic_cooldown_seconds=0, agent_cooldown_seconds=0),
    )

    await engine.consume(race_room_event(RaceEventType.PIT_STOP))
    await engine.consume(race_room_event(RaceEventType.PIT_STOP, sequence=2))

    assert len(repository.messages) == 2
    assert engine.metrics.trigger_count == 2
    assert engine.metrics.rejected_message_count == 1
