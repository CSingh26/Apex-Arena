# SPDX-License-Identifier: AGPL-3.0-only
import asyncio
import time

import pytest

from app.services.history_details import DetailUnavailable, HistoryDetailReader, ReadBudget
from app.services.intelligence_context import SessionFactualContext
from tests.test_race_history import fact


@pytest.mark.asyncio
async def test_reader_checks_relevant_budget_before_the_129th_transform(monkeypatch):
    service = HistoryDetailReader(None, algorithm_version="test-v1")
    rows = [
        {**fact(index, lap=index, lap_duration=90).model_dump(), "payload_bytes": 100}
        for index in range(1, 130)
    ]

    async def query(*args):
        batch = rows[:64]
        del rows[:64]
        return batch

    service._query = query
    count = 0
    original = SessionFactualContext.advance_owned

    def advance(self, event):
        nonlocal count
        count += 1
        return original(self, event)

    monkeypatch.setattr(SessionFactualContext, "advance_owned", advance)
    with pytest.raises(DetailUnavailable, match="relevant_transform_limit"):
        await service._scan(
            SessionFactualContext(session_key="history-test"),
            0,
            129,
            ReadBudget(time.monotonic() + 2),
        )
    assert count == 128


@pytest.mark.asyncio
async def test_reader_rejects_scan_budget_before_query_and_payload_before_transform():
    service = HistoryDetailReader(None, algorithm_version="test-v1")
    calls = 0

    async def query(*args):
        nonlocal calls
        calls += 1
        return [
            {
                **fact(1, lap=1, lap_duration=90).model_dump(),
                "payload": None,
                "payload_bytes": 65537,
            }
        ]

    service._query = query
    context = SessionFactualContext(session_key="history-test")
    with pytest.raises(DetailUnavailable, match="source_scan_limit"):
        await service._scan(context, 0, 2049, ReadBudget(time.monotonic() + 2))
    assert calls == 0
    with pytest.raises(DetailUnavailable, match="source_payload_bytes"):
        await service._scan(context, 0, 1, ReadBudget(time.monotonic() + 2))
    assert context.relevant_sequence == 0


def test_cache_ttl_entry_and_total_bytes_are_finite(monkeypatch):
    service = HistoryDetailReader(None, algorithm_version="test-v1")
    for index in range(40):
        service._remember((index,), "x" * 500_000)
    assert len(service._cache) <= 32
    assert service._cache_bytes <= 8 * 1024 * 1024
    assert service._cached((39,)) is not None
    later = time.monotonic() + 61
    monkeypatch.setattr(time, "monotonic", lambda: later)
    assert service._cached((39,)) is None


@pytest.mark.asyncio
async def test_cancelled_before_start_releases_reader_slot():
    service = HistoryDetailReader(None, algorithm_version="test-v1")

    async def forever(*args, **kwargs):
        await asyncio.Event().wait()

    service._read = forever
    task = asyncio.create_task(service.read("race", drivers=[4], families=["laps"], cursor=1))
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)
    assert not service._inflight


@pytest.mark.asyncio
async def test_invalid_selection_is_not_misclassified_as_capacity_pressure():
    from app.services.history_details import DetailSelectionError

    service = HistoryDetailReader(None, algorithm_version="test-v1")
    entered = asyncio.Event()
    count = 0

    async def forever(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            entered.set()
        await asyncio.Event().wait()

    service._read = forever
    tasks = [
        asyncio.create_task(service.read("race", drivers=[driver], families=["laps"], cursor=1))
        for driver in (4, 16)
    ]
    try:
        await entered.wait()
        with pytest.raises(DetailSelectionError):
            await service.read("race", drivers=[4], families=["everything"], cursor=1)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
