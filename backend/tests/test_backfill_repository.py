# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.storage.backfill_repository import SqlOpenF1BackfillJobRepository
from app.storage.database import Base
from app.storage.models import OpenF1BackfillJobRecord


class AsyncSQLiteSession:
    def __init__(self, engine: Engine) -> None:
        self._session = Session(engine)

    async def __aenter__(self) -> AsyncSQLiteSession:
        return self

    async def __aexit__(self, *_: object) -> None:
        self._session.close()

    async def execute(self, statement: object):
        return self._session.execute(statement)  # type: ignore[arg-type]

    async def commit(self) -> None:
        self._session.commit()


class SQLiteDatabase:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def session_factory(self) -> AsyncSQLiteSession:
        return AsyncSQLiteSession(self.engine)


@contextmanager
def repository_with_job() -> Iterator[SqlOpenF1BackfillJobRepository]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            session.add(
                OpenF1BackfillJobRecord(
                    season=2026,
                    meeting_key="55",
                    session_key="1001",
                    room_slug="2026-australian-grand-prix-qualifying",
                    status="running",
                    requested_endpoints=["drivers", "laps"],
                    completed_endpoints=["drivers"],
                    cursor_state={
                        "current_endpoint": "laps",
                        "drivers": {
                            "completed_at": "2026-03-07T06:00:00+00:00",
                            "rows_fetched": 20,
                        },
                    },
                    rows_fetched=20,
                    rows_processed=19,
                    rows_inserted=17,
                    rows_deduplicated=2,
                )
            )
            session.commit()
        yield SqlOpenF1BackfillJobRepository(SQLiteDatabase(engine))  # type: ignore[arg-type]
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_empty_endpoint_remains_retryable_with_durable_empty_cursor() -> None:
    with repository_with_job() as repository:
        record = await repository.complete_endpoint(
            2026,
            "1001",
            "laps",
            fetched=0,
            processed=0,
            inserted=0,
            deduplicated=0,
        )

    assert record.completed_endpoints == ["drivers"]
    assert record.cursor_state["current_endpoint"] is None
    assert record.cursor_state["laps"]["rows_fetched"] == 0
    assert "completed_at" not in record.cursor_state["laps"]
    assert datetime.fromisoformat(record.cursor_state["laps"]["last_empty_at"]).tzinfo is not None
    assert (record.rows_fetched, record.rows_processed) == (20, 19)
    assert (record.rows_inserted, record.rows_deduplicated) == (17, 2)


@pytest.mark.asyncio
async def test_nonempty_endpoint_completes_checkpoint_and_accumulates_counters() -> None:
    with repository_with_job() as repository:
        record = await repository.complete_endpoint(
            2026,
            "1001",
            "laps",
            fetched=5,
            processed=4,
            inserted=3,
            deduplicated=1,
        )

    assert record.completed_endpoints == ["drivers", "laps"]
    assert record.cursor_state["current_endpoint"] is None
    assert record.cursor_state["laps"]["rows_fetched"] == 5
    assert "last_empty_at" not in record.cursor_state["laps"]
    assert datetime.fromisoformat(record.cursor_state["laps"]["completed_at"]).tzinfo is not None
    assert (record.rows_fetched, record.rows_processed) == (25, 23)
    assert (record.rows_inserted, record.rows_deduplicated) == (20, 3)
