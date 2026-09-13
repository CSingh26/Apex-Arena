# SPDX-License-Identifier: AGPL-3.0-only
"""Real PostgreSQL transactions in task-owned UUID schemas only."""

import asyncio
import importlib.util
import os
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event as sql_event
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.intelligence import BattleState, BattleStatus
from app.domain.models import EventOrigin, RaceEventType, RaceStateSnapshot
from app.storage.database import Base, Database
from app.storage.models import (
    BattleSummaryRecord,
    NormalizedRaceEventRecord,
    RaceStateSnapshotRecord,
)
from app.storage.repositories import SqlNormalizedEventRepository
from tests.test_race_intelligence import source_event

_OWNED_INTELLIGENCE_TARGETS = {
    ("127.0.0.1", 62485, "replay_test", "apex"),
    ("localhost", 5432, "apex_e2e_ci", "apex"),
}


def _validate_intelligence_sql_target(url):
    parsed = make_url(url)
    target = (parsed.host, parsed.port, parsed.database, parsed.username)
    if (
        parsed.drivername != "postgresql+asyncpg"
        or target not in _OWNED_INTELLIGENCE_TARGETS
        or parsed.query
    ):
        raise ValueError("TEST_INGESTION_POSTGRES_URL must identify task-owned PostgreSQL")
    return url


def _validate_owned_schema_name(schema):
    prefix = "intelligence_test_"
    suffix = schema.removeprefix(prefix)
    if not schema.startswith(prefix) or len(suffix) != 32:
        raise ValueError("intelligence fixture schema must contain a UUID")
    try:
        parsed = UUID(hex=suffix)
    except ValueError as exc:
        raise ValueError("intelligence fixture schema must contain a UUID") from exc
    if parsed.hex != suffix:
        raise ValueError("intelligence fixture schema must contain a UUID")
    return schema


@asynccontextmanager
async def _owned_intelligence_database(url, *, create_all=Base.metadata.create_all):
    _validate_intelligence_sql_target(url)
    database = Database(url)
    schema = _validate_owned_schema_name("intelligence_test_" + uuid4().hex)
    schema_created = False
    original_error = None
    try:
        async with database.engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        schema_created = True
        engine = database.engine.execution_options(schema_translate_map={None: schema})
        database.session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(create_all)
        yield database
    except BaseException as exc:
        original_error = exc
        raise
    finally:
        cleanup_error = None
        if schema_created:
            try:
                async with database.engine.begin() as connection:
                    await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            except BaseException as exc:
                cleanup_error = exc
        try:
            await database.close()
        except BaseException as exc:
            if cleanup_error is None:
                cleanup_error = exc
        if original_error is None and cleanup_error is not None:
            raise cleanup_error


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://apex:local-synthetic@127.0.0.1:62485/replay_test",
        "postgresql+asyncpg://apex:ci-synthetic@localhost:5432/apex_e2e_ci",
    ],
    ids=["owned-local", "ci-service"],
)
def test_intelligence_sql_target_accepts_only_declared_owned_contracts(url):
    assert _validate_intelligence_sql_target(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://apex:test@db.example.com:5432/apex_e2e_ci",
        "postgresql+asyncpg://apex:test@localhost:5432/apex_arena",
        "postgresql+asyncpg://apex:test@localhost:6543/apex_e2e_ci",
        "postgresql+asyncpg://other:test@localhost:5432/apex_e2e_ci",
        "postgresql://apex:test@localhost:5432/apex_e2e_ci",
        "postgresql+asyncpg://apex:test@localhost:5432/apex_e2e_ci?host=db.example.com",
        "postgresql+asyncpg://apex:test@localhost:5432/apex_e2e_ci?service=production",
        "postgresql+asyncpg://apex:test@localhost:5432/apex_e2e_ci?options=-csearch_path%3Dpublic",
    ],
    ids=[
        "remote-host",
        "user-database",
        "wrong-port",
        "wrong-user",
        "wrong-driver",
        "query-host",
        "query-service",
        "query-search-path",
    ],
)
def test_intelligence_sql_target_rejects_unknown_or_redirectable_urls(url):
    with pytest.raises(ValueError, match="task-owned PostgreSQL"):
        _validate_intelligence_sql_target(url)


async def _intelligence_schema_names(url):
    database = Database(url)
    try:
        async with database.engine.connect() as connection:
            rows = await connection.scalars(text("SELECT nspname FROM pg_namespace"))
            return {name for name in rows if name.startswith("intelligence_test_")}
    finally:
        await database.close()


async def test_intelligence_sql_setup_failure_drops_exact_owned_schema():
    url = os.getenv("TEST_INGESTION_POSTGRES_URL")
    if not url:
        pytest.skip("requires task-owned PostgreSQL")
    _validate_intelligence_sql_target(url)
    before = await _intelligence_schema_names(url)

    def fail_create_all(connection):
        raise RuntimeError("synthetic create_all failure")

    with pytest.raises(RuntimeError, match="synthetic create_all failure"):
        async with _owned_intelligence_database(url, create_all=fail_create_all):
            pytest.fail("setup failure must prevent fixture body entry")

    assert await _intelligence_schema_names(url) == before


@pytest.fixture
async def intelligence_sql():
    url = os.getenv("TEST_INGESTION_POSTGRES_URL")
    if not url:
        pytest.skip("requires task-owned PostgreSQL")
    async with _owned_intelligence_database(url) as database:
        yield database


def repository(database):
    assert importlib.util.find_spec("app.storage.intelligence_progress") is not None, (
        "committed sources need a durable critical projection repository"
    )
    from app.storage.intelligence_progress import SqlIntelligenceProgressRepository

    return SqlIntelligenceProgressRepository(database, algorithm_version="test-v1")


def fact(sequence=1):
    return source_event(
        RaceEventType.INTERVAL_SAMPLE, driver=4, interval=1.5, second=sequence, sequence=sequence
    )


def derivation(source, number):
    return source.model_copy(
        update={
            "id": uuid4(),
            "raw_event_id": None,
            "event_origin": EventOrigin.DERIVED,
            "event_type": RaceEventType.BATTLE_STARTED,
            "dedup_key": f"derived-{source.id}-{number}",
        }
    )


async def test_source_pending_and_bundle_completion_are_durable(intelligence_sql):
    repo = repository(intelligence_sql)
    source, new = await repo.append_source(fact(), expected_anchor=(None, 0, 0))
    assert new and source.sequence_number == 1
    progress = await repo.load("race")
    assert progress.pending_source_id == source.id and progress.completed_through_sequence == 0
    derived = await repo.commit_projection(
        source, [derivation(source, 1), derivation(source, 2)], [], expected_anchor=(None, 0, 0)
    )
    assert [row.sequence_number for row in derived] == [2, 3]
    progress = await repository(intelligence_sql).load("race")
    assert progress.pending_source_id is None and progress.completed_through_sequence == 3
    assert progress.completed_source_id == source.id
    duplicate, new = await repo.append_source(
        source.model_copy(update={"sequence_number": 900}), expected_anchor=(source.id, 1, 3)
    )
    assert not new and duplicate.sequence_number == 1


@pytest.mark.parametrize(
    "failure", ["first_derived", "second_derived", "summary", "snapshot", "progress"]
)
async def test_bundle_transaction_rolls_back_without_losing_source(intelligence_sql, failure):
    repo = repository(intelligence_sql)
    source, _ = await repo.append_source(fact(), expected_anchor=(None, 0, 0))
    summary = BattleState(
        id="synthetic-battle",
        session_key="race",
        lead_driver_number=16,
        chasing_driver_number=4,
        lead_position=4,
        chasing_position=5,
        interval_seconds=1.2,
        closest_interval_seconds=1.1,
        started_at=source.event_time,
        last_updated_at=source.event_time,
        status=BattleStatus.RESOLVED,
    )
    snapshot = RaceStateSnapshot(session_key="race", sequence_number=3, state={"synthetic": True})
    count = 0

    def reject(connection, cursor, statement, parameters, context, executemany):
        nonlocal count
        if "INSERT INTO" in statement and "normalized_race_events" in statement:
            count += 1
            if (failure == "first_derived" and count == 1) or (
                failure == "second_derived" and count == 2
            ):
                raise RuntimeError("synthetic derived transaction failure")
        if (
            failure == "progress"
            and statement.startswith("UPDATE")
            and "session_intelligence_progress" in statement
        ):
            raise RuntimeError("synthetic progress transaction failure")
        if "INSERT INTO" in statement and (
            (failure == "summary" and "battle_summaries" in statement)
            or (failure == "snapshot" and "race_state_snapshots" in statement)
        ):
            raise RuntimeError("synthetic summary/snapshot transaction failure")

    sql_event.listen(intelligence_sql.engine.sync_engine, "before_cursor_execute", reject)
    try:
        with pytest.raises(RuntimeError, match="synthetic"):
            await repo.commit_projection(
                source,
                [derivation(source, 1), derivation(source, 2)],
                [summary],
                snapshot,
                expected_anchor=(None, 0, 0),
            )
    finally:
        sql_event.remove(intelligence_sql.engine.sync_engine, "before_cursor_execute", reject)
    records = await SqlNormalizedEventRepository(intelligence_sql).list_for_session("race")
    assert [row.id for row in records] == [source.id]
    assert (await repo.load("race")).pending_source_id == source.id
    async with intelligence_sql.session_factory() as session:
        assert not (await session.scalars(select(BattleSummaryRecord))).all()
        assert not (await session.scalars(select(RaceStateSnapshotRecord))).all()
    await repo.commit_projection(
        source,
        [derivation(source, 1), derivation(source, 2)],
        [summary],
        snapshot,
        expected_anchor=(None, 0, 0),
    )
    assert await SqlNormalizedEventRepository(intelligence_sql).count("race") == 3
    async with intelligence_sql.session_factory() as session:
        assert len((await session.scalars(select(BattleSummaryRecord))).all()) == 1
        assert len((await session.scalars(select(RaceStateSnapshotRecord))).all()) == 1


async def test_legacy_baseline_is_explicitly_unverified_and_never_rewritten(intelligence_sql):
    stored = fact(7)
    await SqlNormalizedEventRepository(intelligence_sql).insert(stored)
    repo = repository(intelligence_sql)
    progress = await repo.initialize("race")
    assert progress.completed_through_sequence == 7
    assert progress.historical_effects_unverified
    assert progress.completed_source_id == stored.id
    assert await SqlNormalizedEventRepository(intelligence_sql).count("race") == 1


async def test_second_writer_cannot_commit_a_pending_source_twice(intelligence_sql):
    repo = repository(intelligence_sql)
    source, _ = await repo.append_source(fact(), expected_anchor=(None, 0, 0))
    results = await asyncio.gather(
        repo.commit_projection(source, [derivation(source, 1)], [], expected_anchor=(None, 0, 0)),
        repository(intelligence_sql).commit_projection(
            source, [derivation(source, 1)], [], expected_anchor=(None, 0, 0)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, list) for result in results) == 1
    assert await SqlNormalizedEventRepository(intelligence_sql).count("race") == 2
    async with intelligence_sql.session_factory() as session:
        assert (await session.scalars(select(NormalizedRaceEventRecord))).all()


async def test_maintenance_writer_refuses_a_live_owner_and_owner_loss(intelligence_sql):
    owner = repository(intelligence_sql)
    other_database = Database(os.environ["TEST_INGESTION_POSTGRES_URL"])
    other_database.session_factory = intelligence_sql.session_factory
    assert await intelligence_sql.acquire_ingestor_lease()
    try:
        from app.storage.intelligence_progress import IntelligenceWriterConflictError

        with pytest.raises(IntelligenceWriterConflictError):
            await repository(other_database).append_source(fact(), expected_anchor=(None, 0, 0))
        stored, _ = await owner.append_source(fact(), expected_anchor=(None, 0, 0))
        await owner.commit_projection(stored, [], [], expected_anchor=(None, 0, 0))
        connection = intelligence_sql._ingestor_lease_connection
        await connection.execute(text("SELECT pg_advisory_unlock(1095782232)"))
        with pytest.raises(RuntimeError, match="ownership was lost"):
            await owner.append_source(fact(2), expected_anchor=(stored.id, 1, 1))
        assert await SqlNormalizedEventRepository(intelligence_sql).count("race") == 1
    finally:
        await intelligence_sql.release_ingestor_lease()
        await other_database.close()


async def test_changed_completed_source_anchor_is_not_accepted_as_current(intelligence_sql):
    repo = repository(intelligence_sql)
    source, _ = await repo.append_source(fact(), expected_anchor=(None, 0, 0))
    await repo.commit_projection(source, [], [], expected_anchor=(None, 0, 0))
    async with intelligence_sql.session_factory() as session:
        row = await session.get(NormalizedRaceEventRecord, source.id)
        row.sequence_number = 90
        await session.commit()
    from app.storage.intelligence_progress import IntelligenceWriterConflictError

    with pytest.raises(IntelligenceWriterConflictError, match="prefix"):
        await repo.initialize("race")
