# SPDX-License-Identifier: AGPL-3.0-only
import importlib.util

import pytest
from sqlalchemy import select

from tests.test_ingestion_recovery import processor, raw
from tests.test_intelligence_commit import intelligence_sql as intelligence_sql


def reader(database):
    assert importlib.util.find_spec("app.services.history_details") is not None, (
        "bounded exact history reader missing"
    )
    from app.services.history_details import HistoryDetailReader

    return HistoryDetailReader(database, algorithm_version="test-v1")


@pytest.mark.asyncio
async def test_detail_does_not_expose_view_or_requested_cursor_ahead_of_ack(intelligence_sql):
    from types import SimpleNamespace

    pipeline, projection, public, _ = processor(intelligence_sql)
    await pipeline.ingest(raw(1, "laps", lap_number=1, lap_duration=90))
    service = reader(intelligence_sql)
    state = await public.get_state("race")
    state.sequence_number = 2

    async def ahead(_):
        return state

    result = await service.read_session(
        "race",
        drivers=[4],
        families=["laps"],
        states=SimpleNamespace(get_state=ahead),
        progress=projection.repository,
    )
    assert result["availability"] == "unavailable"
    assert "data" not in result
    result = await service.read_session(
        "race",
        drivers=[4],
        families=["laps"],
        states=public,
        progress=projection.repository,
        cursor=2,
    )
    assert result["reason"] == "cursor_unacknowledged"


@pytest.mark.asyncio
async def test_corrupt_compact_snapshot_is_deterministic_unavailable(intelligence_sql):
    from sqlalchemy import update

    from app.storage.models import RaceStateSnapshotRecord

    pipeline, _, _, _ = processor(intelligence_sql)
    await pipeline.ingest(raw(1, "laps", lap_number=1, lap_duration=90))
    for index in range(2, 11):
        await pipeline.ingest(raw(index, "car_data", speed=200 + index))
    async with intelligence_sql.session_factory() as session:
        row = (await session.execute(select(RaceStateSnapshotRecord))).scalar_one()
        damaged = dict(row.state)
        damaged["history_sequence"] = "not-a-number"
        await session.execute(update(RaceStateSnapshotRecord).values(state=damaged))
        await session.commit()
    result = await reader(intelligence_sql).read("race", drivers=[4], families=["laps"], cursor=10)
    assert result["availability"] == "unavailable"
    assert result["reason"] == "invalid_compact_snapshot"


@pytest.mark.asyncio
async def test_full_suffix_retention_authority_precedes_driver_selection(intelligence_sql):
    pipeline, projection, public, _ = processor(intelligence_sql)
    for number in range(1, 65):
        await pipeline.ingest(
            raw(number, "laps", driver_number=number, lap_number=1, lap_duration=90 + number / 100)
        )
    await pipeline.ingest(raw(65, "laps", driver_number=4, lap_number=2, lap_duration=92))
    base = (await public.get_state("race")).history_reference
    assert base.base_sequence == 65
    await pipeline.ingest(raw(66, "laps", driver_number=65, lap_number=1, lap_duration=89))
    await pipeline.ingest(raw(67, "laps", driver_number=63, lap_number=1, lap_duration=88))
    await pipeline.ingest(
        raw(
            68,
            "race_control",
            driver_number=64,
            message="LAP 1 TIME DELETED",
            category="Other",
            lap_number=1,
        )
    )
    state = await public.get_state("race")
    context = await projection.working_state.export_factual_context("race")
    result = await reader(intelligence_sql).read("race", drivers=[4], families=["laps"], view=state)
    assert result["availability"] == "available"
    assert result["data"] == reader(intelligence_sql)._select(context, [4], ["laps"])
    assert result["data"]["truncation"]["driver_history_truncated"]


@pytest.mark.asyncio
async def test_actual_sql_detail_at_2048_position_scan_ceiling(intelligence_sql):
    import time
    from datetime import timedelta

    from sqlalchemy import insert

    from app.domain.models import RaceEventType
    from app.storage.models import NormalizedRaceEventRecord
    from app.storage.repositories import SqlNormalizedEventRepository
    from tests.test_race_history import fact

    pipeline, _, public, _ = processor(intelligence_sql)
    await pipeline.ingest(raw(1, "laps", lap_number=1, lap_duration=90))
    state = await public.get_state("race")
    events = []
    for sequence in range(2, 2050):
        event = fact(
            sequence,
            RaceEventType.CAR_DATA_SAMPLE if sequence < 2049 else RaceEventType.LAP_COMPLETED,
            lap=1,
            lap_duration=89,
        ).model_copy(
            update={
                "session_key": "race",
                "event_time": state.analysis_time + timedelta(seconds=sequence),
                "dedup_key": f"{sequence:064x}",
            }
        )
        events.append(event)
    async with intelligence_sql.session_factory() as session:
        for offset in range(0, len(events), 64):
            await session.execute(
                insert(NormalizedRaceEventRecord),
                [
                    SqlNormalizedEventRepository._event_values(event)
                    for event in events[offset : offset + 64]
                ],
            )
        await session.commit()
    state.sequence_number = 2049
    state.history_sequence = 2049
    state.history_reference = state.history_reference.model_copy(
        update={"relevant_sequence": 2049, "relevant_event_id": events[-1].id}
    )
    state.analysis_time = events[-1].event_time
    started = time.perf_counter()
    result = await reader(intelligence_sql).read("race", drivers=[4], families=["laps"], view=state)
    print("SQL_SCAN_2048_SECONDS", time.perf_counter() - started)
    assert result["availability"] == "available", result
    assert result["data"]["drivers"]["4"]["laps"][0]["duration_seconds"] == 89


@pytest.mark.asyncio
async def test_detail_uses_exact_relevant_prefix_and_detaches_cache_hits(intelligence_sql):
    pipeline, _, public, _ = processor(intelligence_sql)
    await pipeline.ingest(raw(1, "laps", lap_number=1, lap_duration=90))
    old = await public.get_state("race")
    await pipeline.ingest(raw(2, "laps", lap_number=1, lap_duration=89))
    current = await public.get_state("race")
    service = reader(intelligence_sql)
    before = await service.read(
        "race", drivers=[4], families=["laps"], view=old, projection_status="stale"
    )
    assert before["view_sequence"] == 1
    assert before["projection_status"] == "stale"
    assert before["data"]["drivers"]["4"]["laps"][0]["duration_seconds"] == 90
    latest = await service.read("race", drivers=[4], families=["laps"], view=current)
    assert latest["history_sequence"] == 2
    assert latest["data"]["drivers"]["4"]["laps"][0]["duration_seconds"] == 89
    latest["data"]["drivers"]["4"]["laps"][0]["duration_seconds"] = 999
    again = await service.read("race", drivers=[4], families=["laps"], view=current)
    assert again["data"]["drivers"]["4"]["laps"][0]["duration_seconds"] == 89


@pytest.mark.asyncio
async def test_historical_detail_resolves_before_cursor_not_later_deletion(intelligence_sql):
    pipeline, _, public, _ = processor(intelligence_sql)
    await pipeline.ingest(raw(1, "laps", lap_number=1, lap_duration=90))
    for index in range(2, 20):
        await pipeline.ingest(raw(index, "car_data", speed=200 + index))
    await pipeline.ingest(raw(20, "laps", lap_number=1, lap_duration=88))
    service = reader(intelligence_sql)
    result = await service.read("race", drivers=[4], families=["laps"], cursor=19)
    assert result["view_sequence"] == 19
    assert result["data"]["drivers"]["4"]["laps"][0]["duration_seconds"] == 90
    missing = await service.read("race", drivers=[4], families=["laps"], cursor=9)
    assert missing["availability"] == "unavailable"
    assert missing["reason"] == "compatible_snapshot_missing"


@pytest.mark.asyncio
async def test_detail_selection_and_scan_are_finite(intelligence_sql):
    service = reader(intelligence_sql)
    with pytest.raises(ValueError, match="selection"):
        await service.read("race", drivers=[1, 2, 3], families=["laps"], cursor=1)
    with pytest.raises(ValueError, match="selection"):
        await service.read("race", drivers=[4], families=["everything"], cursor=1)


@pytest.mark.asyncio
async def test_reader_coalesces_four_callers_and_cancellation_does_not_cancel_peer(
    intelligence_sql, monkeypatch
):
    import asyncio

    pipeline, _, public, _ = processor(intelligence_sql)
    await pipeline.ingest(raw(1, "laps", lap_number=1, lap_duration=90))
    state = await public.get_state("race")
    service = reader(intelligence_sql)
    entered, release = asyncio.Event(), asyncio.Event()
    original = service._context
    calls = 0

    async def blocked(*args):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return await original(*args)

    monkeypatch.setattr(service, "_context", blocked)

    async def read():
        return await service.read("race", drivers=[4], families=["laps"], view=state)

    tasks = [asyncio.create_task(read()) for _ in range(4)]
    try:
        await entered.wait()
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="busy"):
            await read()
        tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        release.set()
        results = await asyncio.gather(*tasks[1:])
        assert calls == 1
        results[0]["data"]["drivers"]["4"]["laps"].clear()
        assert len(results[1]["data"]["drivers"]["4"]["laps"]) == 1
    finally:
        release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_reader_has_two_nowait_slots_and_releases_only_after_task_exit(
    intelligence_sql, monkeypatch
):
    import asyncio

    pipeline, _, public, _ = processor(intelligence_sql)
    await pipeline.ingest(raw(1, "laps", lap_number=1, lap_duration=90))
    state = await public.get_state("race")
    service = reader(intelligence_sql)
    entered, release = asyncio.Event(), asyncio.Event()
    original = service._context
    count = 0

    async def blocked(*args):
        nonlocal count
        count += 1
        if count == 2:
            entered.set()
        await release.wait()
        return await original(*args)

    monkeypatch.setattr(service, "_context", blocked)

    async def read(driver):
        return await service.read("race", drivers=[driver], families=["laps"], view=state)

    tasks = [asyncio.create_task(read(driver)) for driver in (4, 5)]
    try:
        await entered.wait()
        with pytest.raises(RuntimeError, match="busy"):
            await read(6)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        release.set()
        result = await read(6)
        assert result["availability"] == "available"
    finally:
        release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["live", "replay"])
async def test_room_delivery_fence_detects_mode_change_and_same_generation_seek(
    intelligence_sql, monkeypatch, mode
):
    import asyncio

    from sqlalchemy import update

    from app.domain.rooms import RoomMode
    from app.storage.models import RaceRoomRecord
    from app.storage.room_repository import SqlRaceRoomRepository
    from tests.test_room_replay import replay_room

    pipeline, projection, public, _ = processor(intelligence_sql)
    await pipeline.ingest(raw(1, "laps", lap_number=1, lap_duration=90))
    for index in range(2, 21):
        await pipeline.ingest(raw(index, "car_data", speed=200 + index))
    repo = SqlRaceRoomRepository(intelligence_sql)

    async def schema_ready():
        pass

    intelligence_sql.require_ingestion_schema = schema_ready
    room = replay_room(session_key="race").model_copy(update={"mode": RoomMode(mode)})
    await repo.upsert_room(room, [])
    await repo.update_playback(room.id, current_event_sequence=0 if mode == "live" else 20)
    service = reader(intelligence_sql)
    assert hasattr(service, "read_room"), "SQL-bound room view fence missing"
    entered, release = asyncio.Event(), asyncio.Event()
    original = service._context

    async def blocked(*args):
        entered.set()
        await release.wait()
        return await original(*args)

    monkeypatch.setattr(service, "_context", blocked)
    task = asyncio.create_task(
        service.read_room(
            room.slug,
            drivers=[4],
            families=["laps"],
            rooms=repo,
            states=public,
            progress=projection.repository,
        )
    )
    try:
        await entered.wait()
        if mode == "replay":
            await repo.update_playback(room.id, current_event_sequence=10)
        else:
            async with intelligence_sql.session_factory() as session:
                await session.execute(
                    update(RaceRoomRecord).where(RaceRoomRecord.id == room.id).values(mode="replay")
                )
                await session.commit()
        release.set()
        with pytest.raises(RuntimeError, match="view changed"):
            await task
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_live_detail_uses_acknowledged_state_not_playback_zero(intelligence_sql):
    from app.domain.rooms import RoomMode
    from app.storage.room_repository import SqlRaceRoomRepository
    from tests.test_room_replay import replay_room

    pipeline, projection, public, _ = processor(intelligence_sql)
    await pipeline.ingest(raw(1, "laps", lap_number=1, lap_duration=90))
    repo = SqlRaceRoomRepository(intelligence_sql)

    async def schema_ready():
        pass

    intelligence_sql.require_ingestion_schema = schema_ready
    room = replay_room(session_key="race").model_copy(update={"mode": RoomMode.LIVE})
    await repo.upsert_room(room, [])
    service = reader(intelligence_sql)
    assert hasattr(service, "read_room"), "live view resolver missing"
    result = await service.read_room(
        room.slug,
        drivers=[4],
        families=["laps"],
        rooms=repo,
        states=public,
        progress=projection.repository,
    )
    assert (await repo.get_playback(room.id)).current_event_sequence == 0
    assert result["view_sequence"] == 1
    assert result["history_sequence"] == 1
