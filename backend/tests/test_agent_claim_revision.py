# SPDX-License-Identifier: AGPL-3.0-only
"""An agent that takes a position must take it back when the facts move.

This is the behaviour that separates a conversation from a ticker: a read
offered earlier in the session is recalled and revised out loud, rather than
quietly disappearing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.claims import AgentClaim, ClaimOutcome, ClaimStatus
from app.domain.models import RaceEventType
from app.domain.rooms import MessageType
from app.domain.strategy_situations import (
    SituationPayload,
    StrategyEvidence,
    StrategySituation,
    StrategySituationKind,
    StrategyTransition,
)
from app.services.discussion import RaceRoomDiscussionEngine
from app.services.discussion_triggers import DiscussionTriggerEvaluator
from app.services.event_importance import EventImportancePolicy
from app.services.strategy_events import strategy_event, validated_strategy
from tests.fixtures.race_room_events import race_room_event
from tests.test_race_room_discussion import FakeRoomRepository

START = datetime(2026, 7, 17, 12, tzinfo=UTC)
SESSION = "test-race-room"


class InMemoryClaimMemory:
    """The persistent memory's contract, without requiring PostgreSQL."""

    def __init__(self) -> None:
        self.claims: dict[object, AgentClaim] = {}

    async def record(self, claim: AgentClaim) -> AgentClaim:
        self.claims[claim.claim_id] = claim
        return claim

    async def recall(
        self, room_id, *, discussion_generation, cursor, subjects=None, **_kwargs
    ) -> list[AgentClaim]:
        return [
            claim
            for claim in self.claims.values()
            if claim.room_id == room_id
            and claim.discussion_generation == discussion_generation
            and claim.source_sequence <= cursor
            and claim.is_open
        ]

    async def revise(self, claim_id, *, outcome, superseded_by=None) -> None:
        claim = self.claims[claim_id]
        self.claims[claim_id] = claim.model_copy(
            update={
                "status": ClaimStatus.REVISED if superseded_by else ClaimStatus.WITHDRAWN,
                "outcome": outcome,
                "superseded_by": superseded_by,
            }
        )


def weather_transition(
    *,
    sequence: int,
    status: str = "active",
    transition: str = "opened",
    situation_id=None,
    superseded_revision_id=None,
) -> StrategyTransition:
    """A weather_change situation, the simplest fully valid strategy fact."""
    observed = START + timedelta(seconds=sequence)
    evidence = {}
    for _index in range(2):
        row = StrategyEvidence(
            event_id=uuid4(),
            sequence=sequence,
            observed_at=observed,
            source="fixture",
            session_key=SESSION,
            role="weather",
            family="weather",
        )
        evidence[row.key] = row
    situation = StrategySituation(
        situation_id=situation_id or uuid4(),
        revision_id=uuid4(),
        kind=StrategySituationKind.WEATHER_CHANGE,
        status=status,
        transition=transition,
        superseded_revision_id=superseded_revision_id,
        participants=[],
        source_anchor=uuid4(),
        source_sequence=sequence,
        session_key=SESSION,
        sequence=sequence,
        history_sequence=sequence,
        analysis_time=observed,
        semantic_identity="strategy-v1",
        availability="partial",
        payload=SituationPayload(rainfall_before=False, rainfall_now=True),
        evidence_keys=sorted(evidence),
    )
    return StrategyTransition(situation=situation, evidence=evidence)


def situation_event(transition: StrategyTransition):
    source = race_room_event(RaceEventType.WEATHER_CHANGE).model_copy(
        update={
            "sequence_number": transition.situation.sequence,
            "received_at": transition.situation.analysis_time,
        }
    )
    event = strategy_event(source, transition)
    assert event is not None, "fixture must produce a valid strategy event"
    # The pipeline classifies importance before the room ever sees an event, and
    # agent eligibility depends on it, so apply the real policy rather than
    # hand-picking a level the production path would not have assigned.
    importance, score, _emit = EventImportancePolicy().classify(event)
    return event.model_copy(update={"importance_level": importance, "importance_score": score})


def engine_with_memory() -> tuple[
    RaceRoomDiscussionEngine, FakeRoomRepository, InMemoryClaimMemory
]:
    repository = FakeRoomRepository()
    memory = InMemoryClaimMemory()
    engine = RaceRoomDiscussionEngine(
        repository,
        DiscussionTriggerEvaluator(topic_cooldown_seconds=0, agent_cooldown_seconds=0),
        claims=memory,
    )
    return engine, repository, memory


def test_the_weather_fixture_is_a_genuinely_valid_strategy_fact():
    assert validated_strategy(situation_event(weather_transition(sequence=10))) is not None


@pytest.mark.asyncio
async def test_an_active_situation_puts_the_agent_on_the_record():
    engine, repository, memory = engine_with_memory()
    await engine.consume(situation_event(weather_transition(sequence=10)))

    assert len(memory.claims) == 1
    claim = next(iter(memory.claims.values()))
    assert claim.room_id == repository.room.id
    assert claim.source_sequence == 10
    assert claim.is_open
    # The claim is attributed to a message the room actually published.
    assert claim.message_id in {message.id for message in repository.messages}


@pytest.mark.asyncio
async def test_a_revised_observation_makes_the_agent_correct_itself_out_loud():
    engine, repository, memory = engine_with_memory()
    identity = uuid4()
    await engine.consume(situation_event(weather_transition(sequence=10, situation_id=identity)))
    claim = next(iter(memory.claims.values()))
    published_before = len(repository.messages)

    await engine.consume(
        situation_event(
            weather_transition(sequence=20, situation_id=identity, transition="revised")
        )
    )

    corrections = [
        message
        for message in repository.messages[published_before:]
        if message.message_type is MessageType.CORRECTION
    ]
    assert corrections, "a revised premise must be spoken to, not silently dropped"
    correction = corrections[0]
    assert correction.agent_id == claim.agent_id, "the agent revises its own position"
    assert correction.reply_to_message_id == claim.message_id
    assert correction.generation_metadata["revised_claim_id"] == str(claim.claim_id)
    # The earlier read is closed, so it cannot be repeated as if it still stood.
    assert not memory.claims[claim.claim_id].is_open
    assert memory.claims[claim.claim_id].outcome is ClaimOutcome.CONTRADICTED


@pytest.mark.asyncio
async def test_a_withdrawn_premise_is_retracted_rather_than_judged_wrong():
    engine, repository, memory = engine_with_memory()
    identity = uuid4()
    await engine.consume(situation_event(weather_transition(sequence=10, situation_id=identity)))
    claim = next(iter(memory.claims.values()))

    await engine.consume(
        situation_event(
            weather_transition(
                sequence=20,
                situation_id=identity,
                status="withdrawn",
                transition="withdrawn",
                superseded_revision_id=uuid4(),
            )
        )
    )

    stored = memory.claims[claim.claim_id]
    assert stored.status is ClaimStatus.WITHDRAWN
    # A retracted premise proves nothing about the agent's judgement.
    assert stored.outcome is ClaimOutcome.UNDECIDED


@pytest.mark.asyncio
async def test_an_unrelated_situation_does_not_disturb_a_standing_position():
    engine, repository, memory = engine_with_memory()
    await engine.consume(situation_event(weather_transition(sequence=10)))
    claim = next(iter(memory.claims.values()))

    await engine.consume(situation_event(weather_transition(sequence=20, transition="revised")))

    assert memory.claims[claim.claim_id].is_open
    assert not [
        message for message in repository.messages if message.message_type is MessageType.CORRECTION
    ]


@pytest.mark.asyncio
async def test_claim_memory_failure_never_suppresses_a_published_fact():
    engine, repository, _ = engine_with_memory()

    class BrokenMemory:
        async def record(self, claim):
            raise RuntimeError("synthetic memory outage")

        async def recall(self, *args, **kwargs):
            raise RuntimeError("synthetic memory outage")

        async def revise(self, *args, **kwargs):
            raise RuntimeError("synthetic memory outage")

    engine.claims = BrokenMemory()
    await engine.consume(situation_event(weather_transition(sequence=10)))

    assert repository.messages, "the factual strategy message must still be published"


@pytest.mark.asyncio
async def test_rooms_without_claim_memory_behave_exactly_as_before():
    repository = FakeRoomRepository()
    engine = RaceRoomDiscussionEngine(
        repository,
        DiscussionTriggerEvaluator(topic_cooldown_seconds=0, agent_cooldown_seconds=0),
    )
    await engine.consume(situation_event(weather_transition(sequence=10)))
    assert repository.messages
