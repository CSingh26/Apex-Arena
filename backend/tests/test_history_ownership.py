# SPDX-License-Identifier: AGPL-3.0-only
import asyncio

import pytest

from tests.test_ingestion_recovery import processor, raw
from tests.test_intelligence_commit import intelligence_sql as intelligence_sql


@pytest.mark.asyncio
async def test_critical_context_churn_invalidates_idle_owner_then_rewarms_exact_prefix(
    intelligence_sql,
):
    pipeline, projection, public, _ = processor(intelligence_sql)
    for index in range(5):
        await pipeline.ingest(
            raw(1, "laps", lap_number=1, lap_duration=90 + index).model_copy(
                update={"session_key": f"race{index}"}
            )
        )
    assert len(projection.working_state._factual_contexts) == 4
    assert "race0" not in projection._ready
    assert (await public.get_state("race0")).drivers["4"].best_lap_duration == 90
    await pipeline.ingest(
        raw(2, "laps", lap_number=2, lap_duration=91).model_copy(update={"session_key": "race0"})
    )
    context = await projection.working_state.export_factual_context("race0")
    assert [lap.lap_number for lap in context.history.drivers["4"].laps] == [1, 2]
    assert (await public.get_state("race0")).drivers["4"].best_lap_duration == 90
    assert not public._factual_contexts


@pytest.mark.asyncio
async def test_all_pinned_contexts_refuse_before_raw_and_cancel_unpins(intelligence_sql):
    pipeline, projection, _, _ = processor(intelligence_sql)
    assert hasattr(projection, "context_pool"), "pinned private context admission missing"
    entered = [asyncio.Event() for _ in range(4)]
    stop = asyncio.Event()

    async def own(index):
        async with projection.context_pool.pin(f"owner{index}"):
            entered[index].set()
            await stop.wait()

    tasks = [asyncio.create_task(own(index)) for index in range(4)]
    try:
        await asyncio.gather(*(event.wait() for event in entered))
        with pytest.raises(RuntimeError, match="context.*busy"):
            await pipeline.ingest(raw(1, "laps", lap_number=1, lap_duration=90))
        assert await pipeline.normalized_repository.count("race") == 0
        assert await pipeline.raw_events.repository.count("race") == 0
        tasks[0].cancel()
        await asyncio.gather(tasks[0], return_exceptions=True)
        await pipeline.ingest(raw(1, "laps", lap_number=1, lap_duration=90))
        assert await pipeline.normalized_repository.count("race") == 1
    finally:
        stop.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
