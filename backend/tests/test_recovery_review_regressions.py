# SPDX-License-Identifier: AGPL-3.0-only
"""Adversarial boundaries from the independent recovery review."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.api.routes import _session_intelligence
from app.services.race_state import RaceState, RaceStateEngine
from app.storage.database import Database
from app.storage.intelligence_progress import IntelligenceWriterConflictError
from tests.test_ingestion_recovery import processor, raw
from tests.test_intelligence_commit import fact, repository
from tests.test_intelligence_commit import intelligence_sql as intelligence_sql


async def test_source_admission_rejects_a_private_predecessor_changed_during_raw_io(
    intelligence_sql,
):
    a, _, _, _ = processor(intelligence_sql)
    b, projection, view, _ = processor(intelligence_sql)
    entered, release = asyncio.Event(), asyncio.Event()
    persist = b.raw_events.persist

    async def paused(value):
        entered.set()
        await release.wait()
        return await persist(value)

    b.raw_events.persist = paused
    item = raw(2, "position", position=5)
    task = asyncio.create_task(b.ingest(item))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await a.ingest(raw(1, "position", driver_number=16, position=4))
        release.set()
        with pytest.raises(IntelligenceWriterConflictError):
            await task
        assert await b.normalized_repository.count("race") == 1
        await b.ingest(item)
        assert sorted((await view.get_state("race")).drivers) == ["16", "4"]
        assert (await projection.repository.load("race")).completed_through_sequence == 2
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_takeover_cannot_activate_until_admitted_old_transaction_finishes(intelligence_sql):
    old = repository(intelligence_sql)
    assert await intelligence_sql.acquire_ingestor_lease()
    next_db = Database(str(intelligence_sql.engine.url.render_as_string(hide_password=False)))
    next_db.session_factory = intelligence_sql.session_factory
    source, _ = await old.append_source(
        fact().model_copy(update={"driver_numbers": [16]}), expected_anchor=(None, 0, 0)
    )
    entered, release = asyncio.Event(), asyncio.Event()
    write = old._write

    @asynccontextmanager
    async def paused_commit(key):
        async with write(key) as session:
            yield session
            entered.set()
            await release.wait()

    old._write = paused_commit
    transaction = asyncio.create_task(
        old.commit_projection(source, [], [], expected_anchor=(None, 0, 0))
    )
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await intelligence_sql._ingestor_lease_connection.invalidate()
        assert not await next_db.acquire_ingestor_lease()
        assert (await repository(next_db).load("race")).pending_source_id == source.id
        release.set()
        await transaction
        assert await next_db.acquire_ingestor_lease()
        with pytest.raises(RuntimeError, match="ownership was lost"):
            await old.append_source(fact(2), expected_anchor=(source.id, 1, 1))
        pipeline, projection, state, _ = processor(next_db)
        await pipeline.ingest(raw(2, "position", position=5))
        assert sorted((await state.get_state("race")).drivers) == ["16", "4"]
        assert (await projection.repository.load("race")).completed_through_sequence == 2
    finally:
        release.set()
        await asyncio.gather(transaction, return_exceptions=True)
        intelligence_sql._ingestor_lease_connection = None
        await next_db.close()


async def test_changed_pending_anchor_during_reconstruction_cannot_acknowledge_old_state(
    intelligence_sql,
):
    a, ap, _, _ = processor(intelligence_sql)
    b, bp, bv, _ = processor(intelligence_sql)
    await ap.repository.append_source(
        fact().model_copy(update={"driver_numbers": [16]}), expected_anchor=(None, 0, 0)
    )
    restore = bp.coordinator.restore_session

    async def crossed(*args, **kwargs):
        await restore(*args, **kwargs)
        await a.recover_session("race")
        await a.ingest(raw(2, "position", position=5))

    bp.coordinator.restore_session = crossed
    with pytest.raises(IntelligenceWriterConflictError):
        await b.recover_session("race")
    bp.coordinator.restore_session = restore
    await b.recover_session("race")
    assert sorted((await bv.get_state("race")).drivers) == ["16", "4"]
    assert (await bp.repository.load("race")).completed_through_sequence == 2


async def test_lost_physical_owner_cannot_reactivate_from_python_flag_and_closes(intelligence_sql):
    assert await intelligence_sql.acquire_ingestor_lease()
    await intelligence_sql._ingestor_lease_connection.invalidate()
    try:
        with pytest.raises(RuntimeError, match="ownership was lost"):
            await intelligence_sql.acquire_ingestor_lease()
    finally:
        await intelligence_sql.release_ingestor_lease()
    assert not intelligence_sql.ingestor_lease_owned


async def test_maintenance_transaction_also_delays_live_activation(intelligence_sql):
    next_db = Database(str(intelligence_sql.engine.url.render_as_string(hide_password=False)))
    try:
        async with repository(intelligence_sql)._write("race"):
            assert not await next_db.acquire_ingestor_lease()
        assert await next_db.acquire_ingestor_lease()
    finally:
        await next_db.close()


async def test_cancelled_takeover_does_not_leave_an_untracked_singleton_lock(
    intelligence_sql, monkeypatch
):
    from sqlalchemy.ext.asyncio import AsyncConnection

    entered = asyncio.Event()
    scalar = AsyncConnection.scalar
    held = []

    async def paused(connection, statement, parameters=None, **kwargs):
        if parameters == {"lock_id": 1_095_782_234}:
            held.append(connection)
            entered.set()
            await asyncio.Event().wait()
        return await scalar(connection, statement, parameters, **kwargs)

    monkeypatch.setattr(AsyncConnection, "scalar", paused)
    task = asyncio.create_task(intelligence_sql.acquire_ingestor_lease())
    next_db = Database(str(intelligence_sql.engine.url.render_as_string(hide_password=False)))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        monkeypatch.setattr(AsyncConnection, "scalar", scalar)
        assert await next_db.acquire_ingestor_lease()
    finally:
        for connection in held:
            if not connection.closed:
                await connection.invalidate()
                await connection.close()
        await next_db.close()


# These tests prove a blocked session is abandoned at a finite bound. The bound
# has to clear real PostgreSQL round-trip latency on the healthy path, or a slow
# machine reports a bounding failure that did not happen.
BLOCKED_PATH_DEADLINE_SECONDS = 0.5
BOUNDED_COMPLETION_ALLOWANCE_SECONDS = 5


async def test_timed_out_pending_session_does_not_hold_the_next_session(intelligence_sql):
    from app.services.container import AppServices

    pipeline, projection, public, _ = processor(intelligence_sql)
    await projection.repository.append_source(fact(), expected_anchor=(None, 0, 0))
    await projection.repository.append_source(
        fact(2).model_copy(
            update={
                "session_key": "second",
                "dedup_key": "synthetic-second",
            }
        ),
        expected_anchor=(None, 0, 0),
    )
    advance = projection.coordinator.advance_source

    async def blocked(source, state):
        if source.session_key == "race":
            await asyncio.Event().wait()
        return await advance(source, state)

    async def diagnostic(*args):
        await asyncio.Event().wait()

    projection.coordinator.advance_source = blocked
    projection.repository.record_failure = diagnostic
    worker = SimpleNamespace(
        processor=pipeline,
        intelligence_progress=projection.repository,
        settings=SimpleNamespace(
            intelligence_recovery_timeout_seconds=BLOCKED_PATH_DEADLINE_SECONDS
        ),
        _intelligence_recovery_cursor="",
    )
    result = await asyncio.wait_for(
        AppServices.recover_pending_intelligence(worker),
        BOUNDED_COMPLETION_ALLOWANCE_SECONDS,
    )
    assert result == {"attempted": 2, "recovered": 1, "failed": 1}
    assert (await public.get_state("second")).sequence_number == 1


async def test_worker_shutdown_cancels_blocked_failure_diagnostic(settings):
    from app.services.container import AppServices

    services = AppServices(settings)
    entered = asyncio.Event()

    async def diagnostic(*args):
        entered.set()
        await asyncio.Event().wait()

    async def fail(*args, **kwargs):
        raise ValueError("synthetic failure")

    projection = services.processor.critical_projection
    projection.repository.record_failure = diagnostic
    projection.working_state.apply = fail
    services._intelligence_recovery_task = asyncio.create_task(
        projection._project(fact(), (None, 0, 0))
    )
    await asyncio.wait_for(entered.wait(), 1)
    await asyncio.wait_for(services.close(), 1)
    assert services._intelligence_recovery_task is None


@pytest.mark.parametrize("publication_fails", [False, True])
async def test_final_recovery_refreshes_split_reader_or_labels_stale_present_cache(
    intelligence_sql, publication_fails
):
    pipeline, projection, _, notified = processor(intelligence_sql)
    cache = RaceState(session_key="race")
    publications = []

    async def publish(state):
        nonlocal cache
        publications.append(state.sequence_number)
        if publication_fails:
            raise ConnectionError("synthetic unavailable cache")
        cache = state.model_copy(deep=True)

    async def read(key):
        return cache.model_copy(deep=True)

    projection.state_publisher = publish
    await projection.repository.append_source(fact(), expected_anchor=(None, 0, 0))
    await pipeline.recover_session("race")
    reader = RaceStateEngine(projection.public_state.snapshots, live_state_reader=read)
    response = await _session_intelligence(
        SimpleNamespace(
            race_state=reader,
            intelligence_progress=projection.repository,
        ),
        "race",
    )
    assert publications == [1]
    assert response.projection.status == ("stale" if publication_fails else "current")
    assert response.sequence_number == (0 if publication_fails else 1)
    assert notified.events == []
    from app.api.room_routes import race_room_detail
    from tests.test_room_routes import api_room, route_services

    room = api_room().model_copy(update={"session_key": "race"})
    services = route_services(room)
    services.race_state = reader
    services.intelligence_progress = projection.repository
    room_response = await race_room_detail(room.slug, services)
    assert room_response.intelligence.sequence_number == response.sequence_number
    assert room_response.intelligence.projection.status == response.projection.status
    # An explicitly earlier replay view must not be labelled stale live state.
    await reader.install_state(RaceState(session_key="race", is_replay=True))
    replay_response = await race_room_detail(room.slug, services)
    assert replay_response.intelligence.sequence_number == 0
    assert replay_response.intelligence.projection.status == "replay"


@pytest.mark.parametrize("cancel", [False, True])
async def test_failure_diagnostics_cannot_hold_attempt_past_deadline(intelligence_sql, cancel):
    pipeline, projection, _, _ = processor(intelligence_sql)
    entered = asyncio.Event()

    async def advance(*args):
        if cancel:
            await asyncio.Event().wait()
        raise ValueError("synthetic critical failure")

    async def diagnostic(*args):
        entered.set()
        await asyncio.Event().wait()

    projection.coordinator.advance_source = advance
    projection.repository.record_failure = diagnostic

    async def attempt():
        if cancel:
            async with asyncio.timeout(BLOCKED_PATH_DEADLINE_SECONDS):
                await pipeline.ingest(raw(1))
        else:
            await pipeline.ingest(raw(1))

    task = asyncio.create_task(attempt())
    try:
        done, _ = await asyncio.wait({task}, timeout=BOUNDED_COMPLETION_ALLOWANCE_SECONDS)
        assert done, "diagnostic I/O held recovery past its bound"
        with pytest.raises(TimeoutError if cancel else ValueError):
            await task
        assert not entered.is_set() if cancel else entered.is_set()
        assert (await projection.repository.load("race")).pending_source_id is not None
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
