# SPDX-License-Identifier: AGPL-3.0-only
import asyncio
import importlib.util
from datetime import timedelta

import pytest

from app.domain.models import EventOrigin
from app.services.event_pipeline import (
    EventDeduplicator,
    EventOrderingBuffer,
    RaceEventProcessor,
    SequenceNumberService,
)
from app.services.normalization import OpenF1EventNormalizer
from app.services.race_intelligence import RaceIntelligenceCoordinator
from app.services.race_state import RaceStateEngine
from app.services.raw_events import RawEventInput, RawProviderEventService
from app.storage.repositories import (
    SqlNormalizedEventRepository,
    SqlRaceStateSnapshotRepository,
    SqlRawEventRepository,
)
from tests.test_event_pipeline import Consumer
from tests.test_intelligence_commit import fact, repository
from tests.test_intelligence_commit import intelligence_sql as intelligence_sql
from tests.test_race_intelligence import START


def processor(database):
    assert importlib.util.find_spec("app.services.intelligence_recovery") is not None, (
        "critical projection must recover committed sources without provider retransmission"
    )
    from app.services.intelligence_recovery import IntelligenceProjection

    snapshots = SqlRaceStateSnapshotRepository(database)
    public = RaceStateEngine(snapshots)
    working = RaceStateEngine(snapshots, retain_applied_dedup_keys=False)
    coordinator = RaceIntelligenceCoordinator(working)
    normalized = SqlNormalizedEventRepository(database)
    projection = IntelligenceProjection(repository(database), normalized, coordinator, public)
    notified = Consumer()
    pipeline = RaceEventProcessor(
        raw_events=RawProviderEventService(SqlRawEventRepository(database)),
        normalizer=OpenF1EventNormalizer(),
        normalized_repository=normalized,
        deduplicator=EventDeduplicator(),
        ordering_buffer=EventOrderingBuffer(0),
        sequence_numbers=SequenceNumberService(normalized),
        consumers=[notified],
        critical_projection=projection,
    )
    return pipeline, projection, public, notified


def raw(index, endpoint="intervals", **payload):
    return RawEventInput(
        provider_endpoint=endpoint,
        session_key="race",
        event_time=START + timedelta(seconds=index),
        raw_payload={
            "driver_number": 4,
            "interval": 1.5,
            "normalized_session_type": "RACE",
            **payload,
        },
    )


@pytest.mark.parametrize("restart", [False, True])
async def test_committed_source_recovers_after_partial_coordinator_failure(
    intelligence_sql, restart
):
    pipeline, projection, public, notified = processor(intelligence_sql)
    advance = projection.coordinator.advance_source

    async def fail_after_advance(event, state):
        await advance(event, state)
        raise RuntimeError("synthetic partial detector failure")

    projection.coordinator.advance_source = fail_after_advance
    with pytest.raises(RuntimeError, match="synthetic"):
        await pipeline.ingest(raw(1))
    assert await pipeline.normalized_repository.count("race") == 1
    assert notified.events == []
    assert (await public.get_state("race")).sequence_number == 0
    assert (await projection.repository.load("race")).pending_source_id is not None
    if restart:
        pipeline, projection, public, notified = processor(intelligence_sql)
    else:
        projection.coordinator.advance_source = advance
    await pipeline.recover_session("race")
    assert (await public.get_state("race")).drivers["4"].interval == 1.5
    assert (await projection.repository.load("race")).pending_source_id is None
    assert notified.events == []  # recovery is not historical discussion/publication
    await pipeline.ingest(raw(2, interval=1.2))
    assert (await public.get_state("race")).drivers["4"].interval == 1.2
    assert len(notified.events) == 1


async def test_derived_commit_failure_retries_without_losing_candidates(intelligence_sql):
    pipeline, projection, public, notified = processor(intelligence_sql)
    await pipeline.ingest(raw(1, "position", driver_number=16, position=4))
    await pipeline.ingest(raw(2, "position", position=5))
    await pipeline.ingest(raw(3, interval=1.8))
    await pipeline.ingest(raw(4, interval=1.7))
    commit = projection.repository.commit_projection

    async def fail(source, derived, summaries, snapshot=None, **kwargs):
        assert derived
        raise RuntimeError("synthetic derived commit outage")

    projection.repository.commit_projection = fail
    before_notifications = len(notified.events)
    with pytest.raises(RuntimeError, match="synthetic"):
        await pipeline.ingest(raw(5, interval=1.6))
    assert len(notified.events) == before_notifications
    projection.repository.commit_projection = commit
    await pipeline.recover_session("race")
    events = await pipeline.normalized_repository.list_for_session("race", limit=100)
    assert any(event.event_origin is EventOrigin.DERIVED for event in events)
    assert len({event.dedup_key for event in events}) == len(events)
    assert len((await public.get_state("race")).current_battles) == 1
    assert len(notified.events) == before_notifications


async def test_ambiguous_committed_bundle_is_reloaded_without_republication(intelligence_sql):
    pipeline, projection, public, notified = processor(intelligence_sql)
    commit = projection.repository.commit_projection

    async def committed_then_lost(*args, **kwargs):
        await commit(*args, **kwargs)
        raise ConnectionError("synthetic post-commit disconnect")

    projection.repository.commit_projection = committed_then_lost
    with pytest.raises(ConnectionError):
        await pipeline.ingest(raw(1))
    projection.repository.commit_projection = commit
    await pipeline.recover_session("race")
    await pipeline.ingest(raw(1))
    assert await pipeline.normalized_repository.count("race") == 1
    assert (await public.get_state("race")).sequence_number == 1
    assert notified.events == []


async def test_appservices_uses_critical_projection_not_best_effort_reducers(
    intelligence_sql, settings
):
    from app.services.container import AppServices

    services = AppServices(settings)
    services.database.session_factory = intelligence_sql.session_factory
    services.race_state.live_state_reader = None
    services.processor.ordering_buffer = EventOrderingBuffer(0)
    notifications = Consumer()
    services.processor.consumers = [services.race_state, services.race_intelligence, notifications]

    async def broken_advance(*args, **kwargs):
        raise RuntimeError("synthetic critical failure")

    services.race_intelligence._advance = broken_advance
    try:
        with pytest.raises(RuntimeError, match="synthetic critical failure"):
            await services.processor.ingest(raw(1))
        assert notifications.events == []
        assert (await services.race_state.get_state("race")).sequence_number == 0
    finally:
        await services.close()


async def test_periodic_worker_recovers_final_pending_fact_without_provider_input(
    intelligence_sql, settings
):
    from types import SimpleNamespace

    from app.services.container import AppServices

    pipeline, projection, public, notified = processor(intelligence_sql)
    commit = projection.repository.commit_projection

    async def failed(*args, **kwargs):
        raise RuntimeError("synthetic outage")

    projection.repository.commit_projection = failed
    with pytest.raises(RuntimeError):
        await pipeline.ingest(raw(1))
    projection.repository.commit_projection = commit
    worker = SimpleNamespace(
        processor=pipeline,
        intelligence_progress=projection.repository,
        settings=settings,
        _intelligence_recovery_cursor="",
    )
    result = await AppServices.recover_pending_intelligence(worker)
    assert result == {"attempted": 1, "recovered": 1, "failed": 0}
    assert (await public.get_state("race")).sequence_number == 1
    assert notified.events == []


async def test_flush_failure_preserves_not_yet_committed_buffered_facts(intelligence_sql):
    pipeline, projection, public, _ = processor(intelligence_sql)
    pipeline.ordering_buffer = EventOrderingBuffer(60_000)
    await pipeline.ingest(raw(1))
    await pipeline.ingest(raw(2, interval=1.2))
    commit = projection.repository.commit_projection

    async def unavailable(*args, **kwargs):
        raise RuntimeError("synthetic outage")

    projection.repository.commit_projection = unavailable
    with pytest.raises(RuntimeError):
        await pipeline.flush_session("race")
    projection.repository.commit_projection = commit
    await pipeline.flush_session("race")
    assert (await public.get_state("race")).drivers["4"].interval == 1.2
    assert await pipeline.normalized_repository.count("race") == 2


async def test_pending_intelligence_is_visible_in_public_projection(intelligence_sql):
    from types import SimpleNamespace

    from app.api.routes import _session_intelligence

    pipeline, projection, public, _ = processor(intelligence_sql)
    await projection.repository.append_source(fact(), expected_anchor=(None, 0, 0))
    response = await _session_intelligence(
        SimpleNamespace(
            race_state=public,
            intelligence_progress=projection.repository,
        ),
        "race",
    )
    assert response.projection.status == "pending"
    assert response.projection.pending_source_sequence == 1
    assert response.projection.completed_through_sequence == 0


async def test_historical_adapter_refuses_writer_before_provider_queries():
    from types import SimpleNamespace

    from app.services.historical import HistoricalOpenF1Adapter
    from app.storage.intelligence_progress import IntelligenceWriterConflictError
    from tests.test_historical import FakeOpenF1Client, FakeProcessor, FakeRuns, FakeSnapshots

    async def reject(session_key):
        raise IntelligenceWriterConflictError("synthetic active owner")

    fake = FakeProcessor()
    fake.critical_projection = SimpleNamespace(
        repository=SimpleNamespace(check_writer_access=reject)
    )
    client = FakeOpenF1Client({})
    adapter = HistoricalOpenF1Adapter(
        client=client,
        processor=fake,
        runs=FakeRuns(),
        snapshots=FakeSnapshots(),
        max_records_per_endpoint=100,
    )
    with pytest.raises(IntelligenceWriterConflictError):
        await adapter.ingest_session("race", ["laps"])
    assert client.queries == []


async def test_snapshot_boundary_is_the_completed_bundle_not_the_next_sequence(intelligence_sql):
    pipeline, projection, public, _ = processor(intelligence_sql)
    public.snapshot_every_n_events = 3
    for index in (1, 2):
        await pipeline.ingest(raw(index, interval=2 + index / 10))
    assert await public.snapshots.latest("race") is None
    await pipeline.ingest(raw(3, interval=2.3))
    assert (await public.snapshots.latest("race")).sequence_number == 3


async def test_cancelled_projection_recovers_and_other_session_advances(intelligence_sql):
    pipeline, projection, public, notified = processor(intelligence_sql)
    entered = asyncio.Event()
    advance = projection.coordinator.advance_source

    async def blocked(source, state):
        if source.session_key == "race":
            await advance(source, state)
            entered.set()
            await asyncio.Event().wait()
        return await advance(source, state)

    projection.coordinator.advance_source = blocked
    pending = asyncio.create_task(pipeline.ingest(raw(1)))
    await asyncio.wait_for(entered.wait(), 2)
    try:
        await asyncio.wait_for(
            pipeline.ingest(raw(2).model_copy(update={"session_key": "other"})), 2
        )
        assert (await public.get_state("other")).sequence_number == 1
        assert (await public.get_state("race")).sequence_number == 0
    finally:
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    assert (await projection.repository.load("race")).pending_source_id is not None
    projection.coordinator.advance_source = advance
    await pipeline.recover_session("race")
    assert (await public.get_state("race")).sequence_number == 1
    assert [event.session_key for event in notified.events] == ["other"]


async def test_recovery_worker_checks_schema_and_joins_cancelled_sweep(settings):
    from unittest.mock import AsyncMock

    from app.services.container import AppServices

    services = AppServices(settings)
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def sweep():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    services.recover_pending_intelligence = sweep
    services.database.require_ingestion_schema = AsyncMock()
    try:
        await services.start_intelligence_recovery()
        assert services._intelligence_recovery_task is None
        services.database._ingestor_lease_connection = object()
        await services.start_intelligence_recovery()
        await asyncio.wait_for(entered.wait(), 2)
        services.database.require_ingestion_schema.assert_awaited_once()
    finally:
        # Synthetic ownership is only a startup branch seam, never a SQL lease.
        services.database._ingestor_lease_connection = None
        await services.close()
    assert cancelled.is_set()
    assert services._intelligence_recovery_task is None


@pytest.mark.parametrize("scenario", ["battle", "pending_overtake", "qualifying"])
async def test_failed_durable_projection_restart_matches_uninterrupted_and_stored_replay(
    intelligence_sql, scenario
):
    from app.domain.models import RaceEventType
    from tests.test_ingestion_restart import qualifying_prefix, race_prefix
    from tests.test_race_intelligence import Snapshots, consume, source_event

    prefix = qualifying_prefix() if scenario == "qualifying" else race_prefix()
    if scenario == "pending_overtake":
        prefix += [
            source_event(RaceEventType.POSITION_SAMPLE, driver=4, position=4, second=6, sequence=6),
            source_event(
                RaceEventType.POSITION_SAMPLE, driver=16, position=5, second=6, sequence=7
            ),
            source_event(
                RaceEventType.INTERVAL_SAMPLE, driver=4, interval=0.7, second=8, sequence=8
            ),
        ]
    pipeline, projection, public, notified = processor(intelligence_sql)
    uninterrupted = RaceStateEngine(Snapshots())
    coordinator = RaceIntelligenceCoordinator(uninterrupted)
    cursor = 0
    for index, source in enumerate(prefix):
        await projection.initialize_session("race")
        if index == len(prefix) - 1:

            async def failed(*args, **kwargs):
                raise RuntimeError("synthetic final-source outage")

            projection.repository.commit_projection = failed
            with pytest.raises(RuntimeError):
                await projection.append(source)
            pipeline, projection, public, notified = processor(intelligence_sql)
            await pipeline.recover_session("race")
        else:
            await projection.append(source)
        cursor += 1
        reference = source.model_copy(update={"sequence_number": cursor})
        for derived in await consume(uninterrupted, coordinator, reference):
            cursor += 1
            await uninterrupted.apply(derived.model_copy(update={"sequence_number": cursor}))
    assert (await public.get_state("race")).model_dump() == (
        await uninterrupted.get_state("race")
    ).model_dump()
    assert projection.coordinator.diagnostics_for_session(
        "race"
    ) == coordinator.diagnostics_for_session("race")
    assert notified.events == []
    restored = RaceStateEngine(Snapshots())
    replay = RaceIntelligenceCoordinator(restored)
    await replay.restore_session("race", pipeline.normalized_repository)
    assert (await restored.get_state("race")).model_dump() == (
        await public.get_state("race")
    ).model_dump()
    assert (await projection.repository.load("race")).completed_through_sequence == cursor
