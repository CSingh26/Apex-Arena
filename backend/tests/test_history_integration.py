# SPDX-License-Identifier: AGPL-3.0-only
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.models import EventOrigin, RaceEventType
from app.services.race_state import RaceState, RaceStateEngine
from tests.test_race_history import fact
from tests.test_race_state import SnapshotRepository

START = datetime(2026, 9, 12, tzinfo=UTC)


@pytest.mark.asyncio
async def test_batch_retains_one_session_context_scope_through_final_flush():
    from contextlib import asynccontextmanager

    from app.services.event_pipeline import (
        EventDeduplicator,
        EventOrderingBuffer,
        RaceEventProcessor,
        SequenceNumberService,
    )
    from app.services.normalization import OpenF1EventNormalizer
    from app.services.raw_events import RawEventInput, RawProviderEventService
    from tests.test_event_pipeline import NormalizedRepository, RawRepository

    repository = NormalizedRepository()
    pipeline = RaceEventProcessor(
        raw_events=RawProviderEventService(RawRepository()),
        normalizer=OpenF1EventNormalizer(),
        normalized_repository=repository,
        deduplicator=EventDeduplicator(),
        ordering_buffer=EventOrderingBuffer(1500),
        sequence_numbers=SequenceNumberService(repository),
    )
    scopes = []
    original = pipeline._session_scope

    @asynccontextmanager
    async def observed(key):
        scopes.append(key)
        async with original(key):
            yield

    pipeline._session_scope = observed
    await pipeline.ingest_batch(
        [
            RawEventInput(
                provider_endpoint="position",
                session_key="race",
                raw_payload={"driver_number": 4, "position": value},
            )
            for value in (1, 2)
        ]
    )
    assert scopes == ["race"]
    assert await repository.count("race") == 2
    assert pipeline.ordering_buffer.pending() == 0


@pytest.mark.asyncio
async def test_discarded_intelligence_return_avoids_copy_but_default_stays_detached(monkeypatch):
    engine = RaceStateEngine(SnapshotRepository())
    await engine.apply(fact(1, lap=1, lap_duration=90))

    def forbidden(*args, **kwargs):
        raise AssertionError("discarded compact copy")

    with monkeypatch.context() as guard:
        guard.setattr(RaceState, "model_copy", forbidden)
        assert (
            await engine.set_intelligence(
                "history-test", current_battles=[], qualifying=None, return_state=False
            )
            is None
        )
    returned = await engine.set_intelligence("history-test", current_battles=[], qualifying=None)
    returned.drivers["4"].last_lap.clear()
    assert (await engine.get_state("history-test")).drivers["4"].last_lap


@pytest.mark.asyncio
@pytest.mark.parametrize("meaning", [{"drs_permission": "disabled"}, {"neutralization": "green"}])
async def test_control_without_changed_lap_dependencies_skips_summaries_but_updates_clock(
    monkeypatch, meaning
):
    from app.services import race_state

    engine = RaceStateEngine(SnapshotRepository())
    await engine.apply(control(1, 0, neutralization="green"))
    await engine.apply(fact(2, RaceEventType.WEATHER_UPDATE, rainfall=0))
    await engine.apply(
        fact(3, lap=1, lap_duration=90, date_start=(START + timedelta(seconds=10)).isoformat())
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("unchanged driver summary recomputed")

    with monkeypatch.context() as guard:
        guard.setattr(race_state, "estimate_pace", forbidden)
        guard.setattr(race_state, "estimate_pit_loss", forbidden)
        state = await engine.apply(control(4, 400, **meaning))
    assert state.analysis_time == START + timedelta(seconds=400)
    assert state.weather_analysis.availability == "stale"
    assert state.weather_analysis.age_seconds == 398


@pytest.mark.asyncio
async def test_control_retention_eviction_still_invalidates_lap_cleanliness():
    engine = RaceStateEngine(SnapshotRepository())
    await engine.apply(control(1, 0, neutralization="green"))
    await engine.apply(
        fact(2, lap=1, lap_duration=90, date_start=(START + timedelta(seconds=10)).isoformat())
    )
    for sequence in range(3, 122):
        await engine.apply(control(sequence, 200 + sequence, drs_permission="disabled"))
    assert (
        "control_unknown"
        not in (await engine.export_factual_context("history-test"))
        .history.drivers["4"]
        .laps[0]
        .exclusions
    )
    await engine.apply(control(122, 500, drs_permission="enabled"))
    assert (
        "control_unknown"
        in (await engine.export_factual_context("history-test"))
        .history.drivers["4"]
        .laps[0]
        .exclusions
    )


@pytest.mark.asyncio
async def test_source_reconstruction_yields_inside_in_memory_page():
    import asyncio

    from app.services.race_intelligence import RaceIntelligenceCoordinator

    engine = RaceStateEngine(SnapshotRepository())
    coordinator = RaceIntelligenceCoordinator(engine)

    class Repository:
        async def list_for_session(self, *args, **kwargs):
            return [fact(index, RaceEventType.CAR_DATA_SAMPLE) for index in range(1, 33)]

    ran = []

    async def peer():
        ran.append(True)

    task = asyncio.create_task(peer())
    try:
        await coordinator.restore_session("history-test", Repository(), through_sequence=32)
        assert ran, "a fully buffered page starved unrelated event-loop work"
    finally:
        await task


@pytest.mark.asyncio
async def test_compact_only_install_requires_reconstruction_before_history_mutation():
    original = RaceStateEngine(SnapshotRepository(), algorithm_version="test-v1")
    first = await original.apply(fact(1, lap=1, lap_duration=90))
    fresh = RaceStateEngine(SnapshotRepository(), algorithm_version="test-v1")
    await fresh.install_state(first)
    with pytest.raises(RuntimeError, match="reconstruction"):
        await fresh.apply(fact(2, lap=2, lap_duration=91))
    assert (await fresh.get_state("history-test")) == first


@pytest.mark.asyncio
async def test_late_lap_correction_keeps_latest_lap_identity_and_compact_pit_summary():
    engine = RaceStateEngine(SnapshotRepository())
    await engine.apply(fact(1, lap=1, lap_duration=90))
    await engine.apply(fact(2, lap=2, lap_duration=91))
    state = await engine.apply(fact(3, lap=1, lap_duration=89))
    driver = state.drivers["4"]
    assert driver.last_lap["lap_number"] == 2
    assert driver.last_lap["lap_duration"] == 91
    assert driver.pit_loss.availability == "insufficient_evidence"


@pytest.mark.asyncio
async def test_post_finish_lap_corrections_do_not_force_checkpoint_every_revision():
    engine = RaceStateEngine(SnapshotRepository(), algorithm_version="test-v1")
    await engine.apply(fact(1, lap=1, lap_duration=90))
    await engine.apply(control(2, 100, lifecycle="finished"))
    before = await engine.prepared_history_checkpoint("history-test")
    await engine.apply(fact(3, lap=1, lap_duration=89))
    after = await engine.prepared_history_checkpoint("history-test")
    assert after is before


def test_owned_history_transform_matches_detached_wrapper_without_copying_owner(monkeypatch):
    from app.domain.strategy import SessionHistory
    from app.services import race_history

    original = SessionHistory(session_key="history-test")
    event = fact(1, lap=1, lap_duration=90)
    expected = race_history.apply_history(original, event)
    assert not original.drivers
    owned = SessionHistory(session_key="history-test")

    def forbidden(*args, **kwargs):
        raise AssertionError("owned transform copied full history")

    monkeypatch.setattr(SessionHistory, "model_copy", forbidden)
    assert race_history._apply_history_owned(owned, event) is owned
    assert owned == expected


def control(sequence, seconds, **meaning):
    event = fact(sequence, RaceEventType.RACE_CONTROL, driver=None, control=meaning)
    event.event_time = START + timedelta(seconds=seconds)
    return event


@pytest.mark.asyncio
async def test_compact_projection_uses_own_completed_lap_and_used_tyre_evidence():
    engine = RaceStateEngine(SnapshotRepository())
    await engine.apply(
        fact(
            1,
            RaceEventType.STINT_UPDATE,
            stint_number=1,
            lap_start=5,
            lap_end=10,
            tyre_age_at_start=3,
            compound="MEDIUM",
        )
    )
    await engine.apply(fact(2, driver=1, lap=12, lap_duration=90))
    state = await engine.apply(fact(3, lap=7, lap_duration=91))
    driver = state.drivers["4"]
    assert driver.completed_lap == 7
    assert driver.tyre_age_laps == 6
    assert driver.tyre_age_basis == "observed_stint_and_driver_completed_lap"
    assert {row.sequence for row in driver.tyre_age_evidence} == {1, 3}
    assert state.current_lap == 12
    assert "history" not in state.model_dump()["control"]
    assert "history" not in state.model_dump()
    driver.tyre_age_evidence[0].source = "caller mutation"
    assert (await engine.get_state("history-test")).drivers["4"].tyre_age_evidence[
        0
    ].source != "caller mutation"


@pytest.mark.asyncio
async def test_canonical_source_analysis_time_is_same_for_late_live_and_recorded_rows():
    engines = [RaceStateEngine(SnapshotRepository()), RaceStateEngine(SnapshotRepository())]
    events = [fact(1, RaceEventType.WEATHER_UPDATE, rainfall=0), fact(2, lap=1, lap_duration=90)]
    events[0].event_time = START + timedelta(seconds=600)
    events[1].event_time = START + timedelta(seconds=100)
    for index, engine in enumerate(engines):
        for event in events:
            await engine.apply(event.model_copy(update={"is_replay": bool(index)}))
        derived = fact(3, RaceEventType.BATTLE_STARTED)
        derived.event_origin = EventOrigin.DERIVED
        derived.event_time = START + timedelta(seconds=1000)
        state = await engine.apply(derived)
        assert state.analysis_time == START + timedelta(seconds=600)
        assert state.history_sequence == 2
        assert (
            RaceState.model_validate_json(state.model_dump_json()).analysis_time
            == state.analysis_time
        )


@pytest.mark.asyncio
async def test_late_control_reconciles_interval_but_keeps_independent_pit_and_deletion():
    engine = RaceStateEngine(SnapshotRepository())
    await engine.apply(control(1, 0, neutralization="green", lifecycle="running"))
    await engine.apply(
        fact(
            2,
            lap=1,
            date_start=(START + timedelta(seconds=10)).isoformat(),
            lap_duration=90,
            is_pit_out_lap=True,
        )
    )
    await engine.apply(control(3, 50, neutralization="safety_car"))
    await engine.apply(fact(4, RaceEventType.LAP_DELETED, lap=1))
    context = await engine.export_factual_context("history-test")
    lap = context.history.drivers["4"].laps[0]
    assert lap.interval_start == START + timedelta(seconds=10)
    assert lap.interval_end == START + timedelta(seconds=100)
    assert lap.interval_authority == "approximate_start_plus_complete_duration"
    assert "provider_lap_start_approximate" in lap.interval_limitations
    assert {"pit_out", "neutralized"} <= set(lap.exclusions)
    assert lap.deleted
    assert max(row.sequence for row in lap.control_evidence) <= 4
    # Partial replacement must not erase the prior grounded interval/control.
    await engine.apply(fact(5, lap=1, lap_duration=89))
    after = await engine.export_factual_context("history-test")
    assert after.history.drivers["4"].laps[0].interval_end == lap.interval_end
    assert "neutralized" in after.history.drivers["4"].laps[0].exclusions
    lap.exclusions.clear()
    assert (
        "neutralized"
        in (await engine.export_factual_context("history-test"))
        .history.drivers["4"]
        .laps[0]
        .exclusions
    )


@pytest.mark.asyncio
async def test_exact_older_deletion_retracts_best_without_deleting_latest():
    engine = RaceStateEngine(SnapshotRepository())
    await engine.apply(fact(1, lap=1, lap_duration=88, session_phase="Q1"))
    await engine.apply(fact(2, lap=2, lap_duration=90, session_phase="Q1"))
    state = await engine.apply(fact(3, RaceEventType.LAP_DELETED, lap=1))
    driver = state.drivers["4"]
    assert driver.best_lap_duration == 90
    assert driver.best_laps_by_phase == {"Q1": 90}
    assert driver.last_lap.get("deleted") is not True
    assert driver.latest_lap_duration == 90


@pytest.mark.asyncio
async def test_irrelevant_events_do_not_transform_history_or_advance_relevant_revision(monkeypatch):
    engine = RaceStateEngine(SnapshotRepository())
    await engine.apply(fact(1, lap=1, lap_duration=90))
    from app.services import intelligence_context

    def forbidden(*args, **kwargs):
        raise AssertionError("full history traversal on irrelevant source")

    monkeypatch.setattr(intelligence_context, "apply_history", forbidden)
    for sequence in range(2, 12):
        state = await engine.apply(fact(sequence, RaceEventType.CAR_DATA_SAMPLE, speed=200))
        assert state.history_sequence == 1
    assert state.sequence_number == 11


@pytest.mark.asyncio
async def test_reconstructed_context_survives_next_append_without_future_or_lost_history():
    from app.services.race_intelligence import RaceIntelligenceCoordinator

    events = [
        control(1, 0, neutralization="green"),
        fact(2, RaceEventType.STINT_UPDATE, stint_number=1, lap_start=1, tyre_age_at_start=2),
        fact(3, lap=1, lap_duration=90, date_start=(START + timedelta(seconds=10)).isoformat()),
    ]

    class Rows:
        async def list_for_session(self, session_key, *, after_sequence, limit):
            return [row for row in events if row.sequence_number > after_sequence][:limit]

    engine = RaceStateEngine(SnapshotRepository())
    await RaceIntelligenceCoordinator(engine).restore_session(
        "history-test", Rows(), through_sequence=3
    )
    state = await engine.apply(fact(4, lap=2, lap_duration=91))
    context = await engine.export_factual_context("history-test")
    assert [lap.lap_number for lap in context.history.drivers["4"].laps] == [1, 2]
    assert state.drivers["4"].tyre_age_laps == 4
    assert context.control.neutralization.value == "green"


@pytest.mark.asyncio
async def test_pace_and_weather_summary_inputs_are_gated_but_freshness_uses_source_clock(
    monkeypatch,
):
    engine = RaceStateEngine(SnapshotRepository())
    await engine.apply(control(1, 0, neutralization="green"))
    await engine.apply(
        fact(2, RaceEventType.STINT_UPDATE, stint_number=1, lap_start=1, tyre_age_at_start=0)
    )
    await engine.apply(fact(3, RaceEventType.WEATHER_UPDATE, rainfall=0, track_temperature=30))
    for sequence in range(4, 7):
        await engine.apply(
            fact(
                sequence,
                lap=sequence - 3,
                lap_duration=90,
                date_start=(START + timedelta(seconds=10 + 90 * (sequence - 4))).isoformat(),
            )
        )
    state = await engine.get_state("history-test")
    assert state.drivers["4"].pace.representative_seconds == 90
    assert state.drivers["4"].pace.sample_count == 3
    from app.services import race_state

    def forbidden(*args, **kwargs):
        raise AssertionError("unchanged input recomputed")

    monkeypatch.setattr(race_state, "estimate_pace", forbidden)
    monkeypatch.setattr(race_state, "analyze_weather", forbidden)
    late = fact(7, RaceEventType.CAR_DATA_SAMPLE, speed=200)
    late.event_time = START + timedelta(seconds=400)
    state = await engine.apply(late)
    assert state.weather_analysis.availability == "stale"
    assert state.weather_analysis.age_seconds == 397
    assert state.drivers["4"].pace.representative_seconds == 90


@pytest.mark.asyncio
async def test_agent_grounding_never_recomputes_used_age_from_leader_lap():
    from app.services.agent_grounding import AgentEventEnvelope
    from app.services.session_realtime import timing_state

    engine = RaceStateEngine(SnapshotRepository())
    await engine.apply(
        fact(
            1,
            RaceEventType.STINT_UPDATE,
            stint_number=1,
            lap_start=5,
            tyre_age_at_start=3,
            compound="MEDIUM",
        )
    )
    await engine.apply(fact(2, driver=1, lap=12, lap_duration=90))
    state = await engine.apply(fact(3, lap=7, lap_duration=91))
    assert AgentEventEnvelope._driver_fact(4, state).tyre_age_laps == 6
    assert (
        next(row for row in timing_state(state).drivers if row.driver_number == 4).tyre_age_laps
        == 6
    )


@pytest.mark.asyncio
async def test_retention_cannot_certify_retained_minimum_as_all_session_best():
    engine = RaceStateEngine(SnapshotRepository())
    for sequence in range(1, 122):
        state = await engine.apply(
            fact(sequence, lap=sequence, lap_duration=90, session_phase="Q1"),
            persist_snapshot=False,
        )
    assert state.drivers["4"].best_lap_duration is None
    assert state.drivers["4"].best_lap_availability == "partial"
    assert state.drivers["4"].best_laps_by_phase == {}


@pytest.mark.asyncio
async def test_real_recorded_replay_reset_ticks_and_seek_preserve_durable_snapshots():
    import asyncio

    from app.domain.models import RaceStateSnapshot
    from tests.test_room_replay import coordinator, replay_event, replay_room

    room = replay_room()
    rows = [replay_event(index, index) for index in range(1, 12)]
    snapshots = SnapshotRepository()
    saved = RaceStateSnapshot(
        session_key=room.session_key,
        sequence_number=10,
        snapshot_time=START,
        state=RaceState(session_key=room.session_key, sequence_number=10).model_dump(mode="json"),
        created_at=START,
    )
    await snapshots.insert(saved)
    service, _, _, _, _, _ = coordinator(room, rows)
    service.race_state = RaceStateEngine(snapshots)
    try:
        await service.start(room, restart=True)
        await asyncio.wait_for(service._tasks[room.id], 1)
        await service.seek_to_sequence(room, 10)
        assert [row.id for row in snapshots.snapshots] == [saved.id]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_private_reconstruction_transfer_is_single_use_and_never_copies_history(monkeypatch):
    from app.services.intelligence_context import SessionFactualContext

    scratch = RaceStateEngine(SnapshotRepository())
    target = RaceStateEngine(SnapshotRepository())
    await scratch.apply(fact(1, lap=1, lap_duration=90))
    assert hasattr(scratch, "_take_reconstruction"), "single-use owned transfer missing"

    def forbidden(*args, **kwargs):
        raise AssertionError("cold owner transfer deep-copied full context")

    with monkeypatch.context() as patch:
        patch.setattr(SessionFactualContext, "model_copy", forbidden)
        transfer = await scratch._take_reconstruction("history-test")
        await target._install_reconstruction(transfer)
        with pytest.raises(RuntimeError, match="consumed"):
            await target._install_reconstruction(transfer)
    assert not scratch._factual_contexts
    state = await target.get_state("history-test")
    state.drivers["4"].best_lap_duration = 999
    assert (await target.get_state("history-test")).drivers["4"].best_lap_duration == 90


@pytest.mark.asyncio
async def test_replay_context_pin_survives_operation_worker_handoff_and_idle_eviction():
    from tests.test_room_replay import coordinator, replay_event, replay_room

    room = replay_room()
    service, rooms, _, _, _, _ = coordinator(
        room, [replay_event(index, index) for index in range(1, 12)], interval=0.01
    )
    service.race_state = RaceStateEngine(SnapshotRepository())
    assert hasattr(service, "context_pool"), "replay private context pool missing"
    try:
        await service.start(room)
        assert service.context_pool._pins[room.session_key] == 1
        await service.pause(room)
        assert service.context_pool._pins[room.session_key] == 0
        state = await service.race_state.get_state(room.session_key)
        for key in ("other1", "other2"):
            async with service.context_pool.pin(key):
                pass
        assert room.session_key not in service.race_state._factual_contexts
        assert (
            await service.race_state.get_state(room.session_key)
        ).sequence_number == state.sequence_number
        await service.seek_to_sequence(room, 5)
        assert (await service.race_state.get_state(room.session_key)).sequence_number == 5
        assert (
            await service.race_state.export_factual_context(room.session_key)
        ).relevant_sequence == 5
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_conflicting_pause_does_not_cancel_healthy_pinned_worker():
    from app.services.room_replay import ReplayUnavailableError
    from app.storage.room_repository import ReplayWriteBusyError
    from tests.test_room_replay import coordinator, replay_event, replay_room

    room = replay_room()
    service, rooms, _, _, _, _ = coordinator(
        room, [replay_event(index, index) for index in range(1, 12)], interval=0.1
    )
    try:
        await service.start(room)
        worker = service._tasks[room.id]
        original = rooms.update_playback

        async def busy(*args, **kwargs):
            raise ReplayWriteBusyError("busy; retry")

        rooms.update_playback = busy
        with pytest.raises(ReplayUnavailableError):
            await service.pause(room)
        assert service._tasks[room.id] is worker and not worker.done()
        assert service.context_pool._pins[room.session_key] == 1
        rooms.update_playback = original
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("restart", [False, True])
async def test_post_pause_retirement_does_not_cancel_concurrent_resume_or_replacement(restart):
    import asyncio

    from app.domain.rooms import RoomStatus
    from tests.test_room_replay import coordinator, replay_event, replay_room

    room = replay_room()
    service, _, _, _, _, _ = coordinator(
        room, [replay_event(index, index) for index in range(1, 12)], interval=0.1
    )
    pending = None
    original = service._publish

    async def publish(room, playback, status):
        nonlocal pending
        result = await original(room, playback, status)
        if status == RoomStatus.PAUSED:
            pending = asyncio.create_task(
                service.start(room, restart=True) if restart else service.resume(room)
            )
            await asyncio.sleep(0)
        return result

    service._publish = publish
    try:
        await service.start(room)
        await service.pause(room)
        await pending
        assert not service._tasks[room.id].done()
        assert service.context_pool._pins[room.session_key] == 1
    finally:
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await service.close()
