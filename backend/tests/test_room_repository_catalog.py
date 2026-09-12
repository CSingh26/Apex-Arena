# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.domain.rooms import RoomStatus, SessionType
from app.storage.database import Base
from app.storage.models import RaceRoomRecord
from app.storage.room_repository import SqlRaceRoomRepository


class RefusingSchemaDatabase:
    def __init__(self) -> None:
        self.session_opened = False

    async def require_ingestion_schema(self) -> None:
        raise RuntimeError("schema is behind")

    def session_factory(self) -> None:
        self.session_opened = True
        raise AssertionError("write transaction opened before schema validation")


class AsyncSQLiteSession:
    def __init__(self, engine: object) -> None:
        self._session = Session(engine)  # type: ignore[arg-type]

    async def __aenter__(self) -> AsyncSQLiteSession:
        return self

    async def __aexit__(self, *_: object) -> None:
        self._session.close()

    async def execute(self, statement: object):
        return self._session.execute(statement)  # type: ignore[arg-type]

    async def commit(self) -> None:
        self._session.commit()


class SQLiteDatabase:
    def __init__(self, engine: object) -> None:
        self.engine = engine

    async def require_ingestion_schema(self) -> None:
        return None

    def session_factory(self) -> AsyncSQLiteSession:
        return AsyncSQLiteSession(self.engine)


def room_record(
    slug: str,
    session_type: SessionType,
    scheduled_start: datetime,
    *,
    round_number: int,
    status: RoomStatus = RoomStatus.PENDING,
    actual_start: datetime | None = None,
) -> RaceRoomRecord:
    return RaceRoomRecord(
        slug=slug,
        event_slug="2026-italian-grand-prix",
        season=2026,
        round_number=round_number,
        race_name="Italian Grand Prix",
        official_name="Italian Grand Prix",
        circuit_name="Monza",
        country="Italy",
        session_type=session_type.value,
        scheduled_start=scheduled_start,
        actual_start=actual_start,
        status=status.value,
        mode="replay",
        source_availability="unavailable",
    )


@pytest.mark.asyncio
async def test_room_writes_stop_before_opening_a_transaction_when_schema_is_behind():
    database = RefusingSchemaDatabase()
    repository = SqlRaceRoomRepository(database)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="schema"):
        await repository.seed_agents([])

    assert database.session_opened is False


@pytest.mark.asyncio
async def test_recent_candidates_include_practice_only_after_the_grace_and_duration_window():
    now = datetime(2026, 9, 6, 18, tzinfo=UTC)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            session.add_all(
                [
                    room_record(
                        "completed-practice",
                        SessionType.PRACTICE_3,
                        now - timedelta(minutes=30),
                        round_number=12,
                        status=RoomStatus.COMPLETED,
                    ),
                    room_record(
                        "eligible-practice",
                        SessionType.PRACTICE_1,
                        now - timedelta(hours=2, minutes=30),
                        round_number=13,
                    ),
                    room_record(
                        "too-recent-practice",
                        SessionType.PRACTICE_2,
                        now - timedelta(minutes=30),
                        round_number=14,
                    ),
                    room_record(
                        "eligible-race",
                        SessionType.RACE,
                        now - timedelta(hours=4, minutes=30),
                        round_number=15,
                    ),
                    room_record(
                        "too-recent-race",
                        SessionType.RACE,
                        now - timedelta(hours=3),
                        round_number=16,
                    ),
                ]
            )
            session.commit()

        repository = SqlRaceRoomRepository(SQLiteDatabase(engine))  # type: ignore[arg-type]
        candidates = await repository.list_recent_reconciliation_candidates(
            now=now,
            lookback_days=7,
            grace_minutes=15,
            limit=10,
        )
        await repository.mark_recent_reconciliation_attempt(
            "completed-practice",
            attempted_at=now,
        )
        restarted_repository = SqlRaceRoomRepository(SQLiteDatabase(engine))  # type: ignore[arg-type]
        after_restart = await restarted_repository.list_recent_reconciliation_candidates(
            now=now,
            lookback_days=7,
            grace_minutes=15,
            limit=1,
        )

        assert [room.slug for room in candidates] == [
            "completed-practice",
            "eligible-practice",
            "eligible-race",
        ]
        assert [room.slug for room in after_restart] == ["eligible-practice"]
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_completed_backfill_candidates_exclude_active_sessions():
    now = datetime.now(UTC)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            session.add_all(
                [
                    room_record(
                        "elapsed-practice",
                        SessionType.PRACTICE_1,
                        now - timedelta(hours=3),
                        round_number=17,
                    ),
                    room_record(
                        "active-race",
                        SessionType.RACE,
                        now - timedelta(minutes=30),
                        round_number=18,
                        status=RoomStatus.LIVE,
                    ),
                    room_record(
                        "recently-started-delayed-practice",
                        SessionType.PRACTICE_2,
                        now - timedelta(hours=3),
                        actual_start=now - timedelta(minutes=30),
                        round_number=20,
                    ),
                    room_record(
                        "confirmed-completed-race",
                        SessionType.RACE,
                        now - timedelta(minutes=30),
                        round_number=19,
                        status=RoomStatus.COMPLETED,
                    ),
                ]
            )
            session.commit()

        repository = SqlRaceRoomRepository(SQLiteDatabase(engine))  # type: ignore[arg-type]
        candidates = await repository.list_completed_backfill_candidates(season=2026)

        assert [room.slug for room in candidates] == [
            "elapsed-practice",
            "confirmed-completed-race",
        ]
    finally:
        engine.dispose()
