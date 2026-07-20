# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.domain.rooms import (
    Confidence,
    EvidenceStatus,
    MessageEvidence,
    MessageTopic,
    MessageType,
    RoomMessage,
)
from app.storage.models import RoomMessageRecord
from app.storage.room_repository import SqlRaceRoomRepository


class FakeScalarResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object:
        return self.value


class FakeSession:
    def __init__(self, *, inserted_id: UUID | None, max_sequence: int = 0) -> None:
        self.inserted_id = inserted_id
        self.max_sequence = max_sequence
        self.added: list[object] = []
        self.committed = False
        self.update_count = 0
        self.insert_sql = ""

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def execute(self, statement: object) -> FakeScalarResult:
        visit_name = getattr(statement, "__visit_name__", "")
        if visit_name == "insert":
            self.insert_sql = str(
                statement.compile(dialect=postgresql.dialect(), compile_kwargs={})
            )
            return FakeScalarResult(self.inserted_id)
        if visit_name == "update":
            self.update_count += 1
            return FakeScalarResult(None)
        if "max(room_messages.sequence)" in str(statement):
            return FakeScalarResult(self.max_sequence)
        return FakeScalarResult(None)

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.committed = True


class FakeDatabase:
    def __init__(self, session: FakeSession) -> None:
        self._session = session

    def session_factory(self) -> FakeSession:
        return self._session


def message(room_id: UUID, *, generation_key: str = "gen-1") -> RoomMessage:
    return RoomMessage(
        room_id=room_id,
        agent_id="nova",
        sequence=0,
        topic=MessageTopic.SESSION,
        message_type=MessageType.OBSERVATION,
        content="The session start is confirmed by timing data.",
        confidence=Confidence.HIGH,
        evidence_status=EvidenceStatus.GROUNDED,
        trigger_event_id=uuid4(),
        generated_by="deterministic",
        generation_key=generation_key,
        generation_version="v1",
    )


def evidence(message_id: UUID) -> MessageEvidence:
    return MessageEvidence(
        message_id=message_id,
        evidence_key="event_type",
        evidence_type="normalized_event",
        source_provider="openf1",
        source_reference=str(uuid4()),
    )


@pytest.mark.asyncio
async def test_duplicate_generation_key_returns_inserted_false_without_side_effects() -> None:
    session = FakeSession(inserted_id=None, max_sequence=4)
    repository = SqlRaceRoomRepository(FakeDatabase(session))  # type: ignore[arg-type]
    room_id = uuid4()

    stored, inserted = await repository.insert_message(message(room_id), [evidence(uuid4())])

    assert inserted is False
    assert stored.sequence == 0
    assert session.added == []
    assert session.update_count == 0
    assert session.committed is False
    assert "ON CONFLICT DO NOTHING" in session.insert_sql
    assert "ON CONSTRAINT" not in session.insert_sql


@pytest.mark.asyncio
async def test_duplicate_trigger_agent_returns_inserted_false_without_integrity_error() -> None:
    session = FakeSession(inserted_id=None, max_sequence=2)
    repository = SqlRaceRoomRepository(FakeDatabase(session))  # type: ignore[arg-type]
    first = message(uuid4(), generation_key="same-trigger-different-generation-key")

    _, inserted = await repository.insert_message(first, [evidence(uuid4())])

    assert inserted is False
    assert session.added == []
    assert session.update_count == 0
    assert "ON CONFLICT DO NOTHING" in session.insert_sql


@pytest.mark.asyncio
async def test_successful_insert_increments_counters_and_preserves_sequence_order() -> None:
    inserted_id = uuid4()
    session = FakeSession(inserted_id=inserted_id, max_sequence=7)
    repository = SqlRaceRoomRepository(FakeDatabase(session))  # type: ignore[arg-type]

    stored, inserted = await repository.insert_message(message(uuid4()), [evidence(uuid4())])

    assert inserted is True
    assert stored.id == inserted_id
    assert stored.sequence == 8
    assert len(session.added) == 1
    assert session.update_count == 1
    assert session.committed is True


def test_room_message_model_uses_active_partial_unique_indexes() -> None:
    indexes = {index.name: index for index in RoomMessageRecord.__table__.indexes}

    trigger = indexes["uq_room_message_active_trigger_agent"]
    generation = indexes["uq_room_message_active_generation_key"]

    assert trigger.unique is True
    assert generation.unique is True
    assert str(trigger.dialect_options["postgresql"]["where"]) == "archived_at IS NULL"
    assert (
        str(generation.dialect_options["postgresql"]["where"])
        == "archived_at IS NULL AND generation_key IS NOT NULL"
    )


def test_legacy_all_row_unique_constraints_are_removed_from_model() -> None:
    constraint_names = {
        constraint.name for constraint in RoomMessageRecord.__table__.constraints
    }

    assert "uq_room_trigger_agent" not in constraint_names
    assert "uq_room_message_generation_key" not in constraint_names
    assert "uq_room_message_sequence" in constraint_names


def test_archived_rows_allow_replacement_but_active_duplicates_remain_blocked() -> None:
    indexes = {index.name: index for index in RoomMessageRecord.__table__.indexes}

    assert "archived_at IS NULL" in str(
        indexes["uq_room_message_active_trigger_agent"].dialect_options["postgresql"]["where"]
    )
    assert "generation_key IS NOT NULL" in str(
        indexes["uq_room_message_active_generation_key"].dialect_options["postgresql"]["where"]
    )


def test_generation_rerun_duplicate_is_skipped_not_failed() -> None:
    result = SimpleNamespace(messages_inserted=0, messages_skipped=0)

    inserted = False
    if not inserted:
        result.messages_skipped += 1

    assert result.messages_inserted == 0
    assert result.messages_skipped == 1
