# SPDX-License-Identifier: AGPL-3.0-only
from datetime import timedelta

import pytest

from app.domain.models import RaceEventType
from app.services.race_intelligence import RaceIntelligenceCoordinator
from app.services.race_state import RaceStateEngine
from tests.test_history_integration import START
from tests.test_intelligence_commit import intelligence_sql as intelligence_sql
from tests.test_race_history import fact
from tests.test_race_state import SnapshotRepository


@pytest.mark.asyncio
async def test_qualifying_retraction_reconciles_coordinator_and_recorded_cursor():
    live = RaceStateEngine(SnapshotRepository())
    coordinator = RaceIntelligenceCoordinator(live)
    recorded = RaceStateEngine(SnapshotRepository())
    rows = [
        fact(1, lap=1, lap_duration=88, normalized_session_type="QUALIFYING", session_phase="Q1"),
        fact(2, lap=2, lap_duration=90, session_phase="Q1"),
        fact(3, RaceEventType.LAP_DELETED, lap=1),
        fact(4, lap=3, lap_duration=89, session_phase="Q1"),
    ]
    original = [row.model_dump() for row in rows]
    for row, expected in zip(rows, [88, 88, 90, 89], strict=True):
        row.event_time = START + timedelta(seconds=row.sequence_number * 60)
        effects = await coordinator.advance_source(
            row, await live.apply(row, persist_snapshot=False)
        )
        current = await live.get_state("history-test")
        replay = await recorded.apply(
            row.model_copy(update={"is_replay": True}), persist_snapshot=False
        )
        assert current.qualifying_intelligence.best_laps == {4: expected}
        assert current.qualifying_intelligence.session_best == expected
        assert replay.qualifying_intelligence.best_laps == {4: expected}
        if row.sequence_number == 4:
            assert {event.event_type for event in effects.derived} == {
                RaceEventType.PERSONAL_BEST,
                RaceEventType.FASTEST_LAP,
            }
    assert [row.payload for row in rows] == [row["payload"] for row in original]


@pytest.mark.asyncio
async def test_late_qualifying_classification_does_not_attribute_old_best_to_slower_source():
    engine = RaceStateEngine(SnapshotRepository())
    coordinator = RaceIntelligenceCoordinator(engine)
    first = fact(1, lap=1, lap_duration=88)
    await coordinator.advance_source(first, await engine.apply(first, persist_snapshot=False))
    current = fact(2, lap=2, lap_duration=90, normalized_session_type="QUALIFYING")
    effects = await coordinator.advance_source(
        current, await engine.apply(current, persist_snapshot=False)
    )
    assert not effects.derived
    assert (await engine.get_state("history-test")).qualifying_intelligence.best_laps == {4: 88}


@pytest.mark.asyncio
async def test_partial_new_lap_never_relabels_payload_of_other_lap():
    engine = RaceStateEngine(SnapshotRepository())
    first = fact(1, lap=7, lap_duration=90, date_start=START.isoformat(), duration_sector_1=30)
    second = fact(
        2,
        lap=8,
        lap_duration=None,
        date_start=(START + timedelta(seconds=120)).isoformat(),
        duration_sector_1=20,
    )
    await engine.apply(first)
    state = await engine.apply(second)
    driver = state.drivers["4"]
    assert driver.completed_lap == 7
    assert driver.last_lap["lap_number"] == 7
    assert driver.last_lap["date_start"] == START.isoformat()
    assert driver.last_lap["duration_sector_1"] == 30
    assert (await engine.export_factual_context("history-test")).history.drivers["4"].laps[
        -1
    ].lap_number == 8


@pytest.mark.asyncio
async def test_old_stint_correction_keeps_current_compound_and_age_identity():
    engine = RaceStateEngine(SnapshotRepository())
    for row in [
        fact(
            1,
            RaceEventType.STINT_UPDATE,
            stint_number=1,
            lap_start=1,
            lap_end=2,
            tyre_age_at_start=0,
            compound="SOFT",
        ),
        fact(
            2,
            RaceEventType.STINT_UPDATE,
            stint_number=2,
            lap_start=3,
            tyre_age_at_start=0,
            compound="HARD",
        ),
        fact(3, lap=4, lap_duration=90),
        fact(
            4,
            RaceEventType.STINT_UPDATE,
            stint_number=1,
            lap_start=1,
            lap_end=2,
            tyre_age_at_start=1,
            compound="SOFT",
        ),
    ]:
        state = await engine.apply(row)
    driver = state.drivers["4"]
    assert driver.stint["stint_number"] == 2
    assert driver.stint["compound"] == "HARD"
    assert driver.tyre_age_laps == 2
    assert [ref.sequence for ref in driver.tyre_age_evidence] == [2, 3]
    assert (await engine.export_factual_context("history-test")).history.drivers["4"].stints[
        0
    ].tyre_age_at_start == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("prior", [False, True])
async def test_interval_overflow_is_unknown_or_preserves_prior_grounding(prior):
    engine = RaceStateEngine(SnapshotRepository())
    if prior:
        await engine.apply(fact(1, lap=1, lap_duration=90, date_start=START.isoformat()))
    row = fact(2, lap=1, lap_duration=90, date_start="9999-12-31T23:59:59+00:00")
    original = row.model_dump()
    await engine.apply(row)
    lap = (await engine.export_factual_context("history-test")).history.drivers["4"].laps[0]
    assert lap.interval_start == (START if prior else None)
    assert lap.interval_end == (START + timedelta(seconds=90) if prior else None)
    assert row.model_dump() == original


@pytest.mark.asyncio
async def test_qualifying_deletion_before_completion_slower_correction_and_uncertainty():
    engine = RaceStateEngine(SnapshotRepository())
    coordinator = RaceIntelligenceCoordinator(engine)

    async def consume(row):
        effects = await coordinator.advance_source(
            row, await engine.apply(row, persist_snapshot=False)
        )
        return (await engine.get_state("history-test")).qualifying_intelligence, effects.derived

    state, _ = await consume(
        fact(1, RaceEventType.LAP_DELETED, lap=1, normalized_session_type="QUALIFYING")
    )
    state, derived = await consume(fact(2, lap=1, lap_duration=80, session_phase="Q1"))
    assert state.best_laps == {} and state.session_best is None and not derived
    await consume(fact(3, lap=2, lap_duration=88, session_phase="Q1"))
    state, _ = await consume(fact(4, lap=2, lap_duration=91, session_phase="Q1"))
    assert state.best_laps == {4: 91}
    assert state.best_laps_by_phase == {"Q1": {4: 91}}
    for index in range(3, 123):
        state, _ = await consume(fact(index + 2, lap=index, lap_duration=92, session_phase="Q1"))
    assert state.best_lap_availability == "partial"
    assert state.best_laps == {} and state.best_laps_by_phase == {} and state.session_best is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,family,wrong", [("QUALIFYING", "Q", "SQ"), ("SPRINT_QUALIFYING", "SQ", "Q")]
)
async def test_lap_phase_cannot_regress_or_cross_family_but_explicit_phase_can_correct(
    kind, family, wrong
):
    engine = RaceStateEngine(SnapshotRepository())
    coordinator = RaceIntelligenceCoordinator(engine)
    rows = [
        fact(1, lap=1, lap_duration=90, normalized_session_type=kind, session_phase=family + "1"),
        fact(2, lap=2, lap_duration=89, session_phase=family + "2"),
        fact(3, lap=1, lap_duration=91, session_phase=family + "1"),
        fact(4, lap=3, lap_duration=90, session_phase=wrong + "3"),
        fact(5, RaceEventType.QUALIFYING_PHASE, session_phase=family + "1"),
        fact(6, RaceEventType.QUALIFYING_PHASE, session_phase="garbage"),
    ]
    for row, expected in zip(
        rows,
        [family + "1", family + "2", family + "2", family + "2", family + "1", family + "1"],
        strict=True,
    ):
        await coordinator.advance_source(row, await engine.apply(row, persist_snapshot=False))
        current = await engine.get_state("history-test")
        assert current.current_phase == expected
        assert current.qualifying_intelligence.phase == expected
        assert not set(current.qualifying_intelligence.best_laps_by_phase) - {
            family + str(index) for index in (1, 2, 3)
        }

    class Repository:
        async def max_sequence(self, key):
            return len(rows)

        async def list_for_session(self, key, after_sequence=0, limit=1000):
            return rows[after_sequence : after_sequence + limit]

    rebuilt = RaceStateEngine(SnapshotRepository())
    await RaceIntelligenceCoordinator(rebuilt).restore_session("history-test", Repository())
    assert (
        await rebuilt.get_state("history-test")
    ).qualifying_intelligence == current.qualifying_intelligence


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,phase", [("QUALIFYING", "SQ3"), ("SPRINT_QUALIFYING", "Q3")])
async def test_lap_normalizer_does_not_translate_wrong_family_into_current_phase(kind, phase):
    from uuid import uuid4

    from app.services.normalization import OpenF1EventNormalizer
    from tests.test_ingestion_recovery import raw

    incoming = raw(
        1, "laps", lap_number=1, lap_duration=90, normalized_session_type=kind, session_phase=phase
    )
    original = incoming.model_dump()
    row = (
        OpenF1EventNormalizer()
        .normalize(incoming, uuid4())
        .model_copy(update={"sequence_number": 1})
    )
    state = await RaceStateEngine(SnapshotRepository()).apply(row, persist_snapshot=False)
    assert state.current_phase is None
    assert state.qualifying_intelligence.best_laps_by_phase == {}
    assert incoming.model_dump() == original


@pytest.mark.asyncio
async def test_sql_qualifying_retraction_preserves_derived_rows_and_rebuilds_exact_bests(
    intelligence_sql,
):
    from app.domain.models import EventOrigin
    from tests.test_ingestion_recovery import processor, raw

    pipeline, projection, public, _ = processor(intelligence_sql)
    rows = [
        raw(
            60,
            "laps",
            lap_number=1,
            lap_duration=88,
            normalized_session_type="QUALIFYING",
            session_phase="Q1",
        ),
        raw(
            120,
            "laps",
            lap_number=2,
            lap_duration=90,
            normalized_session_type="QUALIFYING",
            session_phase="Q1",
        ),
        raw(
            180,
            "race_control",
            message="LAP TIME DELETED",
            lap_number=1,
            normalized_session_type="QUALIFYING",
        ),
        raw(
            240,
            "laps",
            lap_number=3,
            lap_duration=89,
            normalized_session_type="QUALIFYING",
            session_phase="Q1",
        ),
    ]
    await pipeline.ingest(rows[0])
    immutable = [
        row.model_dump()
        for row in await pipeline.normalized_repository.list_for_session("race", limit=100)
    ]
    for row, expected in zip(rows[1:], [88, 90, 89], strict=True):
        await pipeline.ingest(row)
        state = await public.get_state("race")
        assert state.qualifying_intelligence.best_laps == {4: expected}
        assert state.qualifying_intelligence.session_best == expected
    recorded = await pipeline.normalized_repository.list_for_session("race", limit=100)
    assert [row.model_dump() for row in recorded[: len(immutable)]] == immutable
    assert [row.event_type for row in recorded if row.event_origin is EventOrigin.DERIVED][-2:] == [
        RaceEventType.PERSONAL_BEST,
        RaceEventType.FASTEST_LAP,
    ]
    rebuilt = RaceStateEngine(SnapshotRepository(), algorithm_version="test-v1")
    coordinator = RaceIntelligenceCoordinator(rebuilt)
    await coordinator.restore_session("race", pipeline.normalized_repository)
    assert (
        await rebuilt.get_state("race")
    ).qualifying_intelligence == state.qualifying_intelligence
    assert coordinator.drain_derived("race") == []
    replay = RaceStateEngine(SnapshotRepository())
    for row in recorded:
        view = await replay.apply(
            row.model_copy(update={"is_replay": True}), persist_snapshot=False
        )
        if row.event_type is RaceEventType.LAP_DELETED:
            assert view.qualifying_intelligence.best_laps == {4: 90}
    assert view.qualifying_intelligence.best_laps == {4: 89}
    from tests.test_room_replay import coordinator as replay_coordinator
    from tests.test_room_replay import replay_room

    room = replay_room(session_key="race")
    service, _, _, _, _, _ = replay_coordinator(room, recorded)
    service.race_state = RaceStateEngine(SnapshotRepository())
    deleted = next(
        row.sequence_number for row in recorded if row.event_type is RaceEventType.LAP_DELETED
    )
    try:
        for cursor, expected in [
            (deleted, 90),
            (deleted - 1, 88),
            (recorded[-1].sequence_number, 89),
        ]:
            playback = await service.seek_to_sequence(room, cursor)
            assert playback.current_event_sequence == cursor
            assert (
                await service.race_state.get_state("race")
            ).qualifying_intelligence.best_laps == {4: expected}
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_normalized_lap_stint_identity_reaches_timing_grounding_replay_and_restart():
    from uuid import uuid4

    from app.services.agent_grounding import AgentEventEnvelope
    from app.services.normalization import OpenF1EventNormalizer
    from app.services.session_realtime import timing_state
    from tests.test_ingestion_recovery import raw

    normalizer = OpenF1EventNormalizer()
    raws = [
        raw(
            1,
            "stints",
            stint_number=1,
            lap_start=1,
            lap_end=2,
            tyre_age_at_start=0,
            compound="SOFT",
        ),
        raw(2, "stints", stint_number=2, lap_start=3, tyre_age_at_start=0, compound="HARD"),
        raw(
            3,
            "laps",
            lap_number=7,
            lap_duration=90,
            date_start=START.isoformat(),
            duration_sector_1=30,
        ),
        raw(
            4,
            "laps",
            lap_number=8,
            lap_duration=None,
            date_start=(START + timedelta(seconds=120)).isoformat(),
            duration_sector_1=20,
        ),
        raw(
            5,
            "stints",
            stint_number=1,
            lap_start=1,
            lap_end=2,
            tyre_age_at_start=1,
            compound="SOFT",
        ),
    ]
    rows = [
        normalizer.normalize(item, uuid4()).model_copy(update={"sequence_number": index})
        for index, item in enumerate(raws, 1)
    ]
    original = [row.model_dump() for row in rows]

    async def check(engine):
        state = await engine.get_state("race")
        driver = state.drivers["4"]
        assert driver.last_lap["lap_number"] == 7
        assert driver.last_lap["duration_sector_1"] == 30
        assert driver.stint["stint_number"] == 2
        timing = timing_state(state).drivers[0]
        assert timing.tyre_compound == "HARD" and timing.tyre_age_laps == 5
        grounded = AgentEventEnvelope.from_event(rows[-1], state)
        assert grounded.drivers["4"].compound == "HARD" and grounded.drivers["4"].tyre_age_laps == 5

    live = RaceStateEngine(SnapshotRepository())
    replay = RaceStateEngine(SnapshotRepository())
    for row in rows:
        await live.apply(row, persist_snapshot=False)
        await replay.apply(row.model_copy(update={"is_replay": True}), persist_snapshot=False)

    class Repository:
        async def max_sequence(self, key):
            return len(rows)

        async def list_for_session(self, key, after_sequence=0, limit=1000):
            return [row for row in rows if row.sequence_number > after_sequence][:limit]

    restarted = RaceStateEngine(SnapshotRepository())
    await RaceIntelligenceCoordinator(restarted).restore_session("race", Repository())
    for engine in (live, replay, restarted):
        await check(engine)
    from tests.test_room_replay import coordinator as replay_coordinator
    from tests.test_room_replay import replay_room

    room = replay_room(session_key="race")
    service, _, _, _, _, _ = replay_coordinator(room, rows)
    service.race_state = RaceStateEngine(SnapshotRepository())
    try:
        await service.seek_to_sequence(room, 5)
        await check(service.race_state)
    finally:
        await service.close()
    assert [row.model_dump() for row in rows] == original
