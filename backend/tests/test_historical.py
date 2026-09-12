# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import importlib
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.event_pipeline import PipelineResult
from app.services.historical import (
    HistoricalOpenF1Adapter,
    HistoricalRunOwnershipLostError,
    IngestionRunSummary,
)
from app.services.raw_events import RawEventInput
from app.storage.database import Base, Database
from app.storage.models import IngestionRunRecord
from app.storage.repositories import SqlIngestionRunRepository


class FakeOpenF1Client:
    def __init__(self, payloads: dict[str, list[dict[str, Any]]]) -> None:
        self.payloads = payloads
        self.queries: list[tuple[str, str]] = []

    def __getattr__(self, endpoint: str) -> Any:
        async def fetch(*, session_key: str, **filters: Any) -> list[dict[str, Any]]:
            self.queries.append((endpoint, session_key))
            return self.payloads.get(endpoint, [])

        return fetch


class FakeProcessor:
    def __init__(self) -> None:
        self.events: list[RawEventInput] = []

    async def ingest_batch(self, events: list[RawEventInput]) -> PipelineResult:
        self.events.extend(events)
        return PipelineResult(
            raw_inserted=len(events),
            raw_duplicates=1,
            normalized_inserted=len(events),
        )


class FakeRuns:
    def __init__(self) -> None:
        self.run_id = uuid4()
        self.finished: dict[str, Any] | None = None
        self.running = True
        self.heartbeat_count = 0
        self.two_heartbeats = asyncio.Event()
        self.heartbeat_result = True
        self.heartbeat_hook = None

    async def start(self, *, provider: str, session_key: str, metadata: dict[str, Any]) -> UUID:
        assert provider == "openf1"
        assert metadata["adapter"] == "historical_rest"
        return self.run_id

    async def finish(self, run_id: UUID, **values: Any) -> bool:
        assert run_id == self.run_id
        if not self.running:
            return False
        self.running = False
        self.finished = values
        return True

    async def heartbeat(self, run_id: UUID) -> bool:
        assert run_id == self.run_id
        if not self.running:
            return False
        self.heartbeat_count += 1
        if self.heartbeat_count >= 2:
            self.two_heartbeats.set()
        if self.heartbeat_hook is not None:
            self.heartbeat_hook()
        return self.heartbeat_result

    async def latest(self) -> IngestionRunSummary | None:
        return None

    async def fail_running_before(self, cutoff: datetime, *, reason: str) -> int:
        self.failed_before = (cutoff, reason)
        return 1


class FakeSnapshots:
    def __init__(self) -> None:
        self.calls = 0

    async def count(self, session_key: str | None = None) -> int:
        self.calls += 1
        return 2 if self.calls == 1 else 4


class BlockingOpenF1Client(FakeOpenF1Client):
    def __init__(self) -> None:
        super().__init__({})
        self.fetch_started = asyncio.Event()
        self.release_fetch = asyncio.Event()

    def __getattr__(self, endpoint: str) -> Any:
        async def fetch(*, session_key: str, **filters: Any) -> list[dict[str, Any]]:
            self.queries.append((endpoint, session_key))
            self.fetch_started.set()
            await self.release_fetch.wait()
            return []

        return fetch


class FailingSnapshots:
    async def count(self, session_key: str | None = None) -> int:
        raise RuntimeError("snapshot setup failed")


@pytest.fixture
async def sql_runs():
    url = os.environ.get("TEST_INGESTION_POSTGRES_URL")
    if not url:
        pytest.skip("Set TEST_INGESTION_POSTGRES_URL to an isolated local PostgreSQL database")
    from sqlalchemy.engine import make_url

    assert make_url(url).host in {"localhost", "127.0.0.1"}
    database = Database(url)
    schema = "ingestion_test_" + uuid4().hex
    async with database.engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = database.engine.execution_options(schema_translate_map={None: schema})
    database.session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield SqlIngestionRunRepository(database), database
    finally:
        async with database.engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await database.close()


@pytest.mark.asyncio
async def test_reconcile_stale_runs_uses_an_age_bounded_retryable_failure() -> None:
    now = datetime(2026, 9, 12, 12, tzinfo=UTC)
    runs = FakeRuns()
    service = HistoricalOpenF1Adapter(
        client=FakeOpenF1Client({}),  # type: ignore[arg-type]
        processor=FakeProcessor(),  # type: ignore[arg-type]
        runs=runs,
        snapshots=FakeSnapshots(),
        max_records_per_endpoint=500,
    )

    count = await service.reconcile_stale_runs(now=now, stale_after=timedelta(minutes=30))

    assert count == 1
    assert runs.failed_before == (
        datetime(2026, 9, 12, 11, 30, tzinfo=UTC),
        "worker interrupted",
    )


@pytest.mark.asyncio
async def test_sql_reconciliation_preserves_fresh_finished_and_nonhistorical_runs(sql_runs) -> None:
    repository, database = sql_runs
    now = datetime.now(UTC)
    stale_id = await repository.start(
        provider="openf1",
        session_key="stale",
        metadata={"adapter": "historical_rest", "checkpoint": "laps"},
    )
    fresh_id = await repository.start(
        provider="openf1",
        session_key="fresh",
        metadata={"adapter": "historical_rest"},
    )
    finished_id = await repository.start(
        provider="openf1",
        session_key="finished",
        metadata={"adapter": "historical_rest"},
    )
    other_id = await repository.start(
        provider="openf1",
        session_key="other",
        metadata={"adapter": "live"},
    )
    event_at = now - timedelta(hours=3)
    async with database.session_factory() as session:
        stale = await session.get(IngestionRunRecord, stale_id)
        stale.started_at = now - timedelta(hours=2)
        stale.heartbeat_at = None
        stale.last_event_at = event_at
        stale.raw_inserted = 11
        stale.duplicates = 4
        stale.normalized_inserted = 7
        fresh = await session.get(IngestionRunRecord, fresh_id)
        fresh.started_at = now - timedelta(hours=2)
        fresh.heartbeat_at = now
        finished = await session.get(IngestionRunRecord, finished_id)
        finished.started_at = now - timedelta(hours=2)
        finished.heartbeat_at = None
        finished.status = "completed"
        finished.ended_at = now - timedelta(hours=1)
        other = await session.get(IngestionRunRecord, other_id)
        other.started_at = now - timedelta(hours=2)
        other.heartbeat_at = None
        await session.commit()

    count = await repository.fail_running_before(
        now - timedelta(minutes=30),
        reason="worker interrupted",
    )

    assert count == 1
    async with database.session_factory() as session:
        records = {
            record.id: record
            for record in (await session.execute(select(IngestionRunRecord))).scalars()
        }
    stale = records[stale_id]
    assert stale.status == "failed"
    assert stale.ended_at is not None
    assert stale.last_error == "worker interrupted"
    assert stale.run_metadata == {"adapter": "historical_rest", "checkpoint": "laps"}
    assert (stale.raw_inserted, stale.duplicates, stale.normalized_inserted) == (11, 4, 7)
    assert stale.last_event_at == event_at
    assert records[fresh_id].status == "running"
    assert records[finished_id].status == "completed"
    assert records[other_id].status == "running"


@pytest.mark.asyncio
async def test_sql_heartbeat_and_finish_cannot_resurrect_a_reconciled_run(sql_runs) -> None:
    repository, database = sql_runs
    now = datetime.now(UTC)
    run_id = await repository.start(
        provider="openf1",
        session_key="lost-worker",
        metadata={"adapter": "historical_rest"},
    )
    async with database.session_factory() as session:
        record = await session.get(IngestionRunRecord, run_id)
        record.started_at = now - timedelta(hours=2)
        record.heartbeat_at = None
        record.raw_inserted = 3
        await session.commit()

    assert (
        await repository.fail_running_before(
            now - timedelta(minutes=30), reason="worker interrupted"
        )
        == 1
    )
    assert not await repository.heartbeat(run_id)
    assert not await repository.finish(
        run_id,
        status="completed",
        result=PipelineResult(raw_inserted=99, normalized_inserted=99),
        last_event_at=now,
    )

    async with database.session_factory() as session:
        record = await session.get(IngestionRunRecord, run_id)
        assert record.status == "failed"
        assert record.last_error == "worker interrupted"
        assert record.raw_inserted == 3
        assert record.normalized_inserted == 0


@pytest.mark.asyncio
async def test_sql_heartbeat_refreshes_only_a_running_run(sql_runs) -> None:
    repository, database = sql_runs
    run_id = await repository.start(
        provider="openf1",
        session_key="healthy-worker",
        metadata={"adapter": "historical_rest"},
    )
    old = datetime.now(UTC) - timedelta(hours=1)
    async with database.session_factory() as session:
        record = await session.get(IngestionRunRecord, run_id)
        record.heartbeat_at = old
        await session.commit()

    assert await repository.heartbeat(run_id)
    async with database.session_factory() as session:
        record = await session.get(IngestionRunRecord, run_id)
        assert record.heartbeat_at > old

    assert await repository.finish(
        run_id,
        status="completed",
        result=PipelineResult(raw_inserted=2, normalized_inserted=1),
        last_event_at=None,
    )
    assert not await repository.heartbeat(run_id)


@pytest.mark.asyncio
async def test_heartbeat_migration_leaves_legacy_freshness_unknown() -> None:
    url = os.environ.get("TEST_INGESTION_POSTGRES_URL")
    if not url:
        pytest.skip("Set TEST_INGESTION_POSTGRES_URL to an isolated local PostgreSQL database")
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy.engine import make_url

    assert make_url(url).host in {"localhost", "127.0.0.1"}
    database = Database(url)
    schema = "ingestion_migration_test_" + uuid4().hex
    migration = importlib.import_module("migrations.versions.20260912_0017_ingestion_run_heartbeat")
    try:
        async with database.engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            await connection.execute(
                text(
                    "CREATE TABLE ingestion_runs ("
                    "id integer PRIMARY KEY, status varchar(30) NOT NULL, "
                    "started_at timestamptz NOT NULL)"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO ingestion_runs (id, status, started_at) VALUES "
                    "(1, 'running', now() - interval '2 hours'), "
                    "(2, 'completed', now() - interval '3 hours')"
                )
            )

            def upgrade(sync_connection) -> None:
                original_op = migration.op
                migration.op = Operations(MigrationContext.configure(sync_connection))
                try:
                    migration.upgrade()
                finally:
                    migration.op = original_op

            await connection.run_sync(upgrade)
            legacy = (
                await connection.execute(
                    text("SELECT id, heartbeat_at FROM ingestion_runs WHERE id IN (1, 2)")
                )
            ).all()
            await connection.execute(
                text(
                    "INSERT INTO ingestion_runs (id, status, started_at) "
                    "VALUES (3, 'running', now())"
                )
            )
            future_heartbeat = await connection.scalar(
                text("SELECT heartbeat_at FROM ingestion_runs WHERE id = 3")
            )

        assert legacy == [(1, None), (2, None)]
        assert future_heartbeat is not None
    finally:
        async with database.engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await database.close()


@pytest.mark.asyncio
async def test_ingestion_heartbeats_independently_during_a_slow_provider_await() -> None:
    client = BlockingOpenF1Client()
    runs = FakeRuns()
    adapter = HistoricalOpenF1Adapter(
        client=client,  # type: ignore[arg-type]
        processor=FakeProcessor(),  # type: ignore[arg-type]
        runs=runs,
        snapshots=FakeSnapshots(),
        max_records_per_endpoint=500,
        run_heartbeat_seconds=0.01,
    )

    ingestion = asyncio.create_task(adapter.ingest_session("9839", ["laps"]))
    await asyncio.wait_for(client.fetch_started.wait(), timeout=1)
    await asyncio.wait_for(runs.two_heartbeats.wait(), timeout=1)
    client.release_fetch.set()
    await ingestion
    completed_count = runs.heartbeat_count
    await asyncio.sleep(0.03)

    assert completed_count >= 2
    assert runs.heartbeat_count == completed_count
    assert runs.finished and runs.finished["status"] == "completed"


@pytest.mark.asyncio
async def test_setup_failure_after_run_start_finishes_the_run_as_retryable() -> None:
    runs = FakeRuns()
    adapter = HistoricalOpenF1Adapter(
        client=FakeOpenF1Client({}),  # type: ignore[arg-type]
        processor=FakeProcessor(),  # type: ignore[arg-type]
        runs=runs,
        snapshots=FailingSnapshots(),
        max_records_per_endpoint=500,
        run_heartbeat_seconds=0.01,
    )

    with pytest.raises(RuntimeError, match="snapshot setup failed"):
        await adapter.ingest_session("9839", ["laps"])

    assert runs.finished and runs.finished["status"] == "failed"
    assert runs.finished["last_error"] == "RuntimeError"


@pytest.mark.asyncio
async def test_cancellation_finishes_the_run_and_stops_its_heartbeat() -> None:
    client = BlockingOpenF1Client()
    runs = FakeRuns()
    adapter = HistoricalOpenF1Adapter(
        client=client,  # type: ignore[arg-type]
        processor=FakeProcessor(),  # type: ignore[arg-type]
        runs=runs,
        snapshots=FakeSnapshots(),
        max_records_per_endpoint=500,
        run_heartbeat_seconds=0.01,
    )
    ingestion = asyncio.create_task(adapter.ingest_session("9839", ["laps"]))
    await asyncio.wait_for(client.fetch_started.wait(), timeout=1)
    await asyncio.wait_for(runs.two_heartbeats.wait(), timeout=1)

    ingestion.cancel()
    with pytest.raises(asyncio.CancelledError):
        await ingestion
    cancelled_count = runs.heartbeat_count
    await asyncio.sleep(0.03)

    assert runs.finished and runs.finished["status"] == "failed"
    assert runs.finished["last_error"] == "CancelledError"
    assert runs.heartbeat_count == cancelled_count


@pytest.mark.asyncio
async def test_lost_heartbeat_is_retryable_without_becoming_caller_cancellation() -> None:
    client = BlockingOpenF1Client()
    runs = FakeRuns()
    runs.heartbeat_result = False
    adapter = HistoricalOpenF1Adapter(
        client=client,  # type: ignore[arg-type]
        processor=FakeProcessor(),  # type: ignore[arg-type]
        runs=runs,
        snapshots=FakeSnapshots(),
        max_records_per_endpoint=500,
        run_heartbeat_seconds=0.01,
    )

    with pytest.raises(HistoricalRunOwnershipLostError, match="retry"):
        await adapter.ingest_session("9839", ["laps"])

    assert runs.finished and runs.finished["status"] == "failed"
    assert runs.finished["last_error"] == "HistoricalRunOwnershipLostError"


@pytest.mark.asyncio
async def test_external_cancellation_wins_when_it_coincides_with_heartbeat_loss() -> None:
    client = BlockingOpenF1Client()
    runs = FakeRuns()
    runs.heartbeat_result = False
    adapter = HistoricalOpenF1Adapter(
        client=client,  # type: ignore[arg-type]
        processor=FakeProcessor(),  # type: ignore[arg-type]
        runs=runs,
        snapshots=FakeSnapshots(),
        max_records_per_endpoint=500,
        run_heartbeat_seconds=0.01,
    )
    ingestion = asyncio.create_task(adapter.ingest_session("9839", ["laps"]))
    runs.heartbeat_hook = lambda: asyncio.get_running_loop().call_soon(ingestion.cancel)

    with pytest.raises(asyncio.CancelledError):
        await ingestion

    assert runs.finished and runs.finished["status"] == "failed"
    assert runs.finished["last_error"] == "CancelledError"


@pytest.mark.asyncio
async def test_historical_records_use_unified_pipeline_in_event_time_order() -> None:
    client = FakeOpenF1Client(
        {
            "laps": [
                {
                    "_id": 2,
                    "session_key": 9839,
                    "driver_number": 4,
                    "date_start": "2026-07-19T13:02:00Z",
                }
            ],
            "position": [
                {
                    "_id": 1,
                    "session_key": 9839,
                    "driver_number": 4,
                    "date": "2026-07-19T13:01:00Z",
                }
            ],
        }
    )
    processor = FakeProcessor()
    runs = FakeRuns()
    adapter = HistoricalOpenF1Adapter(
        client=client,  # type: ignore[arg-type]
        processor=processor,  # type: ignore[arg-type]
        runs=runs,
        snapshots=FakeSnapshots(),
        max_records_per_endpoint=500,
    )

    result = await adapter.ingest_session("9839", ["laps", "position"])

    assert [event.provider_endpoint for event in processor.events] == ["position", "laps"]
    assert all(event.is_replay for event in processor.events)
    assert result.raw_inserted == 2
    assert result.duplicates == 1
    assert result.normalized_inserted == 2
    assert result.snapshots == 2
    assert runs.finished and runs.finished["status"] == "completed"


@pytest.mark.asyncio
async def test_historical_ingestion_caps_each_endpoint() -> None:
    records = [{"_id": number, "session_key": 9839} for number in range(5)]
    processor = FakeProcessor()
    adapter = HistoricalOpenF1Adapter(
        client=FakeOpenF1Client({"weather": records}),  # type: ignore[arg-type]
        processor=processor,  # type: ignore[arg-type]
        runs=FakeRuns(),
        snapshots=FakeSnapshots(),
        max_records_per_endpoint=2,
    )

    result = await adapter.ingest_session("9839", ["weather"])

    assert result.fetched_records == 2
    assert len(processor.events) == 2


def test_historical_ingestion_rejects_unsupported_endpoints() -> None:
    with pytest.raises(ValueError, match="Unsupported historical endpoints"):
        HistoricalOpenF1Adapter._validate_endpoints(["laps", "secrets"])


def test_historical_payload_time_is_timezone_aware() -> None:
    parsed = HistoricalOpenF1Adapter._payload_time({"date": "2026-07-19T13:00:00"})

    assert parsed == datetime(2026, 7, 19, 13, tzinfo=UTC)


@pytest.mark.asyncio
async def test_high_frequency_ingestion_keeps_the_latest_sample_for_each_driver() -> None:
    client = FakeOpenF1Client(
        {
            "laps": [{"date_start": "2026-07-19T13:10:00Z", "session_key": 9839}],
            "location": [
                {"driver_number": 4, "date": "2026-07-19T13:09:00Z", "x": 1},
                {"driver_number": 4, "date": "2026-07-19T13:10:00Z", "x": 2},
                {"driver_number": 81, "date": "2026-07-19T13:10:01Z", "x": 3},
            ],
        }
    )
    processor = FakeProcessor()
    adapter = HistoricalOpenF1Adapter(
        client=client,  # type: ignore[arg-type]
        processor=processor,  # type: ignore[arg-type]
        runs=FakeRuns(),
        snapshots=FakeSnapshots(),
        max_records_per_endpoint=500,
    )

    result = await adapter.ingest_session("9839", ["location"])

    assert result.fetched_records == 2
    assert [event.raw_payload["x"] for event in processor.events] == [2, 3]


@pytest.mark.asyncio
async def test_checkpointed_qualifying_result_keeps_resolved_session_type():
    processor = FakeProcessor()
    adapter = HistoricalOpenF1Adapter(
        client=FakeOpenF1Client(
            {
                "session_result": [
                    {
                        "session_key": 901,
                        "driver_number": 16,
                        "position": 1,
                        "duration": [82.612, 82.077, 81.786],
                        "gap_to_leader": [0, 0.195, 0],
                    }
                ]
            }
        ),
        processor=processor,
        runs=FakeRuns(),
        snapshots=FakeSnapshots(),
        max_records_per_endpoint=500,
    )
    await adapter.ingest_session("901", ["session_result"], session_type_hint="QUALIFYING")
    payload = processor.events[0].raw_payload
    assert payload["normalized_session_type"] == "QUALIFYING"
    assert len(payload["phase_results"]) == 3


@pytest.mark.asyncio
async def test_car_history_ignores_laps_without_a_timestamp():
    adapter = HistoricalOpenF1Adapter(
        client=FakeOpenF1Client(
            {
                "laps": [
                    {"driver_number": 16, "date_start": "2026-09-05T14:59:00Z"},
                    {"driver_number": 16, "date_start": None},
                ]
            }
        ),
        processor=FakeProcessor(),
        runs=FakeRuns(),
        snapshots=FakeSnapshots(),
        max_records_per_endpoint=500,
    )
    assert await adapter._high_frequency_window_start("901", ["car_data"]) == datetime(
        2026, 9, 5, 14, 54, tzinfo=UTC
    )
