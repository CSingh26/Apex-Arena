# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import Executable

from app.storage.room_repository import SqlRaceRoomRepository


class ScalarResult:
    def __init__(self, value: UUID | None) -> None:
        self.value = value

    def scalar_one_or_none(self) -> UUID | None:
        return self.value


class RecordingSession:
    def __init__(self, selected_room_id: UUID | None) -> None:
        self.selected_room_id = selected_room_id
        self.statements: list[Executable] = []
        self.commit_count = 0

    async def execute(self, statement: Executable) -> ScalarResult:
        self.statements.append(statement)
        return ScalarResult(self.selected_room_id if len(self.statements) == 1 else None)

    async def commit(self) -> None:
        self.commit_count += 1


class SessionContext:
    def __init__(self, session: RecordingSession) -> None:
        self.session = session

    async def __aenter__(self) -> RecordingSession:
        return self.session

    async def __aexit__(self, *_: object) -> None:
        return None


class RecordingDatabase:
    def __init__(self, selected_room_id: UUID | None) -> None:
        self.session = RecordingSession(selected_room_id)

    def session_factory(self) -> SessionContext:
        return SessionContext(self.session)


def compiled(statement: Executable) -> tuple[str, dict[str, Any]]:
    result = statement.compile(dialect=postgresql.dialect())
    return " ".join(str(result).split()), result.params


@pytest.mark.asyncio
async def test_cleanup_selection_requires_every_empty_development_room_guard() -> None:
    room_id = uuid4()
    database = RecordingDatabase(room_id)
    repository = SqlRaceRoomRepository(database)  # type: ignore[arg-type]

    deleted = await repository.delete_empty_development_room(
        "development-day2-validation"
    )

    assert deleted is True
    selection_sql, selection_params = compiled(database.session.statements[0])
    assert "race_rooms.slug =" in selection_sql
    assert "race_rooms.is_development IS true" in selection_sql
    assert "race_rooms.message_count =" in selection_sql
    assert "NOT (EXISTS" in selection_sql
    assert "room_messages.room_id = race_rooms.id" in selection_sql
    assert "FOR UPDATE" in selection_sql
    assert "development-day2-validation" in selection_params.values()
    assert 0 in selection_params.values()

    deletion_sql = [compiled(statement)[0] for statement in database.session.statements[1:]]
    assert len(deletion_sql) == 3
    assert deletion_sql[0].startswith("DELETE FROM room_playback_states")
    assert deletion_sql[1].startswith("DELETE FROM race_room_agents")
    assert deletion_sql[2].startswith("DELETE FROM race_rooms")
    assert all(
        room_id in compiled(statement)[1].values()
        for statement in database.session.statements[1:]
    )
    assert database.session.commit_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "protected_case",
    [
        "ordinary_non-development room",
        "room with nonzero message_count",
        "room with persisted messages despite a stale count",
        "missing or differently named room",
    ],
)
async def test_cleanup_never_deletes_when_any_protection_guard_rejects_room(
    protected_case: str,
) -> None:
    database = RecordingDatabase(selected_room_id=None)
    repository = SqlRaceRoomRepository(database)  # type: ignore[arg-type]

    deleted = await repository.delete_empty_development_room(
        "development-day2-validation"
    )

    assert protected_case
    assert deleted is False
    assert len(database.session.statements) == 1
    assert database.session.commit_count == 0
