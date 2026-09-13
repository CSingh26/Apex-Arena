# SPDX-License-Identifier: AGPL-3.0-only
"""Agent claim memory: cursor-bounded recall, revision and generation scoping."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.claims import AgentClaim, ClaimKind, ClaimOutcome, ClaimStatus
from app.domain.rooms import RoomMode, RoomStatus
from app.domain.strategy_situations import (
    SituationPayload,
    StrategySituation,
    StrategySituationKind,
)
from app.services.agent_claims import AgentClaimMemory
from app.services.claim_reasoning import (
    REVISION_LINES,
    WITHDRAWAL_LINES,
    claim_for_situation,
    closes,
    revision_text,
    situation_key,
)
from app.storage.database import Base, Database
from app.storage.models import AgentProfileRecord, RaceRoomRecord
from tests.test_room_replay import replay_room

ANALYSIS_TIME = datetime(2026, 7, 17, 12, 30, tzinfo=UTC)


def situation(
    *,
    kind: StrategySituationKind = StrategySituationKind.STINT_DIVERGENCE,
    sequence: int = 10,
    status: str = "active",
    transition: str = "opened",
    availability: str = "partial",
    situation_id=None,
    participants: list[int] | None = None,
) -> StrategySituation:
    return StrategySituation(
        situation_id=situation_id or uuid4(),
        revision_id=uuid4(),
        kind=kind,
        status=status,
        transition=transition,
        participants=participants if participants is not None else [4, 16],
        source_anchor=uuid4(),
        source_sequence=sequence,
        session_key="belgian-race-session",
        sequence=sequence,
        history_sequence=sequence,
        analysis_time=ANALYSIS_TIME,
        semantic_identity="strategy-v1",
        availability=availability,
        payload=SituationPayload(),
    )


# --- Deterministic reasoning, no storage required ---------------------------


def test_active_situation_commits_the_agent_to_a_recorded_position():
    item = situation()
    claim = claim_for_situation(
        item, room_id=uuid4(), discussion_generation=1, agent_id="nova", lap_number=15
    )
    assert claim is not None
    assert claim.kind is ClaimKind.STRATEGY_EXPECTATION
    assert claim.subjects == [4, 16]
    assert claim.source_sequence == item.sequence
    assert claim.lap_number == 15
    assert claim.is_open
    # The situation identity is what a later revision matches against.
    assert situation_key(item) in claim.evidence_keys


@pytest.mark.parametrize(
    ("status", "availability"),
    [("withdrawn", "partial"), ("active", "unavailable")],
)
def test_a_situation_that_asserts_nothing_creates_no_claim(status, availability):
    item = situation(status=status, availability=availability)
    assert (
        claim_for_situation(item, room_id=uuid4(), discussion_generation=1, agent_id="nova") is None
    )


def test_weather_situation_is_recorded_as_a_weather_position():
    item = situation(kind=StrategySituationKind.WEATHER_CHANGE, participants=[])
    claim = claim_for_situation(item, room_id=uuid4(), discussion_generation=1, agent_id="nova")
    assert claim is not None
    assert claim.kind is ClaimKind.WEATHER_EXPECTATION
    assert claim.subjects == []


def test_revision_and_withdrawal_close_the_matching_claim_only():
    identity = uuid4()
    opened = situation(situation_id=identity)
    claim = claim_for_situation(opened, room_id=uuid4(), discussion_generation=1, agent_id="nova")
    assert claim is not None

    revised = situation(situation_id=identity, sequence=20, transition="revised")
    withdrawn = situation(situation_id=identity, sequence=21, status="withdrawn")
    unrelated = situation(sequence=22, transition="revised")

    assert closes(claim, revised)
    assert closes(claim, withdrawn)
    assert not closes(claim, unrelated)
    # An unchanged restatement of the same identity is not a decision.
    assert not closes(claim, situation(situation_id=identity, sequence=23))


def test_revision_language_is_conversational_and_stable_across_replays():
    identity = uuid4()
    claim = claim_for_situation(
        situation(situation_id=identity),
        room_id=uuid4(),
        discussion_generation=1,
        agent_id="nova",
    )
    assert claim is not None
    revised = situation(situation_id=identity, sequence=20, transition="revised")
    first = revision_text(claim, revised)
    assert first == revision_text(claim, revised), "replay must read identically"
    assert first.strip()
    assert first in REVISION_LINES
    # A retraction must not be phrased as the agent having been proved wrong, so
    # it has to draw from the withdrawal wording rather than the revision set.
    withdrawn = situation(situation_id=identity, sequence=21, status="withdrawn")
    assert revision_text(claim, withdrawn) in WITHDRAWAL_LINES


# --- Persistent recall against real PostgreSQL ------------------------------


@pytest.fixture
async def claim_memory():
    url = os.environ.get("TEST_REPLAY_POSTGRES_URL")
    if not url:
        pytest.skip("Set TEST_REPLAY_POSTGRES_URL to an isolated local PostgreSQL database")
    from sqlalchemy.engine import make_url

    assert make_url(url).host in {"localhost", "127.0.0.1"}
    database = Database(url)
    schema = "claims_test_" + uuid4().hex
    async with database.engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = database.engine.execution_options(schema_translate_map={None: schema})
    database.session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    room = replay_room().model_copy(
        update={"status": RoomStatus.REPLAYING, "mode": RoomMode.ARCHIVED}
    )
    values = room.model_dump(exclude={"created_at", "updated_at"})
    values["event_slug"] = "belgian-grand-prix"
    async with database.session_factory() as session:
        session.add(
            AgentProfileRecord(
                id="nova",
                display_name="Nova",
                role="Host",
                short_description="Fixture host",
                avatar_key="N",
                specialties=[],
                personality_rules=[],
                speaking_style="Concise",
                supported_topics=["session"],
                active=True,
                sort_order=1,
                ui_accent_key="gold",
            )
        )
        session.add(RaceRoomRecord(**values))
        await session.commit()
    try:
        yield AgentClaimMemory(database, retained=4), room
    finally:
        async with database.engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await database.close()


def stored(room, *, sequence: int, generation: int = 1, subjects=None) -> AgentClaim:
    return AgentClaim(
        claim_id=uuid4(),
        room_id=room.id,
        discussion_generation=generation,
        agent_id="nova",
        kind=ClaimKind.STRATEGY_EXPECTATION,
        source_sequence=sequence,
        subjects=subjects if subjects is not None else [4],
        summary=f"Position recorded at {sequence}.",
        evidence_keys=[f"situation:{sequence}"],
        observed_at=ANALYSIS_TIME,
    )


async def test_recall_never_returns_a_position_the_room_has_not_reached(claim_memory):
    memory, room = claim_memory
    await memory.record(stored(room, sequence=5))
    await memory.record(stored(room, sequence=25))

    early = await memory.recall(room.id, discussion_generation=1, cursor=10)
    assert [claim.source_sequence for claim in early] == [5]

    later = await memory.recall(room.id, discussion_generation=1, cursor=30)
    assert [claim.source_sequence for claim in later] == [25, 5]

    # Seeking back must not leak the position recorded further ahead.
    assert await memory.recall(room.id, discussion_generation=1, cursor=4) == []


async def test_a_revised_claim_drops_out_of_recall_and_records_its_replacement(claim_memory):
    memory, room = claim_memory
    original = await memory.record(stored(room, sequence=5))
    replacement = await memory.record(stored(room, sequence=9))
    await memory.revise(
        original.claim_id,
        outcome=ClaimOutcome.CONTRADICTED,
        superseded_by=replacement.claim_id,
    )

    open_claims = await memory.recall(room.id, discussion_generation=1, cursor=50)
    assert [claim.claim_id for claim in open_claims] == [replacement.claim_id]


async def test_recall_is_scoped_to_the_discussion_generation(claim_memory):
    memory, room = claim_memory
    await memory.record(stored(room, sequence=5, generation=1))
    await memory.record(stored(room, sequence=6, generation=2))

    first = await memory.recall(room.id, discussion_generation=1, cursor=50)
    assert [claim.source_sequence for claim in first] == [5]

    removed = await memory.reset(room.id, discussion_generation=1)
    assert removed == 1
    assert await memory.recall(room.id, discussion_generation=1, cursor=50) == []
    # A reset generation must not disturb another generation's memory.
    survivors = await memory.recall(room.id, discussion_generation=2, cursor=50)
    assert [claim.source_sequence for claim in survivors] == [6]


async def test_memory_stays_bounded_and_keeps_the_most_recent_positions(claim_memory):
    memory, room = claim_memory
    for sequence in range(1, 9):
        await memory.record(stored(room, sequence=sequence))

    retained = await memory.recall(room.id, discussion_generation=1, cursor=100, limit=16)
    assert [claim.source_sequence for claim in retained] == [8, 7, 6, 5]


async def test_recall_filters_to_the_drivers_actually_under_discussion(claim_memory):
    memory, room = claim_memory
    await memory.record(stored(room, sequence=5, subjects=[4]))
    await memory.record(stored(room, sequence=6, subjects=[81]))
    await memory.record(stored(room, sequence=7, subjects=[]))

    about_four = await memory.recall(room.id, discussion_generation=1, cursor=50, subjects=[4])
    # The session-wide position still applies alongside the driver-specific one.
    assert [claim.source_sequence for claim in about_four] == [7, 5]


async def test_a_withdrawn_premise_closes_the_claim_without_declaring_it_wrong(claim_memory):
    memory, room = claim_memory
    claim = await memory.record(stored(room, sequence=5))
    await memory.revise(claim.claim_id, outcome=ClaimOutcome.UNDECIDED)

    assert await memory.recall(room.id, discussion_generation=1, cursor=50) == []
    async with memory.database.session_factory() as session:
        from app.storage.models import AgentClaimRecord

        record = await session.get(AgentClaimRecord, claim.claim_id)
        assert record.status == ClaimStatus.WITHDRAWN.value
        assert record.outcome == ClaimOutcome.UNDECIDED.value
