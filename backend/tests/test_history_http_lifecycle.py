# SPDX-License-Identifier: AGPL-3.0-only
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from app.api.history_routes import router
from app.services.history_details import DetailBusyError, HistoryDetailReader


def request_task(app, path):
    incoming = asyncio.Queue()
    incoming.put_nowait({"type": "http.request", "body": b"", "more_body": False})
    sent = []

    async def send(message):
        sent.append(message)

    task = asyncio.create_task(
        app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": path,
                "raw_path": path.encode(),
                "query_string": b"driver=4&family=laps",
                "headers": [],
                "client": ("127.0.0.1", 1),
                "server": ("synthetic", 80),
                "root_path": "",
            },
            incoming.get,
            send,
        )
    )
    return task, incoming, sent


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    ["/api/v1/sessions/race/intelligence-detail", "/api/v1/race-rooms/test/intelligence-detail"],
)
@pytest.mark.parametrize("peer_finishes", [False, True])
async def test_asgi_disconnect_detaches_one_waiter_and_final_disconnect_cancels_worker(
    path, peer_finishes
):
    reader = HistoryDetailReader(None, algorithm_version="test-v1")
    entered, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def work(*args, **kwargs):
        entered.set()
        try:
            await release.wait()
            return {"availability": "available"}
        finally:
            cancelled.set()

    reader._read = work
    joined = asyncio.Event()
    calls = 0

    async def read(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            asyncio.get_running_loop().call_soon(joined.set)
        return await reader.read("race", drivers=[4], families=["laps"])

    reader.read_session = reader.read_room = read
    app = FastAPI()
    app.include_router(router)
    app.state.services = SimpleNamespace(
        history_details=reader, race_state=None, intelligence_progress=None, room_repository=None
    )
    first, q1, _ = request_task(app, path)
    await entered.wait()
    second, q2, sent = request_task(app, path)

    try:
        await asyncio.wait_for(joined.wait(), 1)
        assert next(iter(reader._inflight.values())).waiters == 2
        q1.put_nowait({"type": "http.disconnect"})
        await asyncio.wait_for(asyncio.shield(first), 0.5)
        assert not cancelled.is_set()
        assert len(reader._inflight) == 1
        assert next(iter(reader._inflight.values())).waiters == 1
        if peer_finishes:
            release.set()
        else:
            q2.put_nowait({"type": "http.disconnect"})
        await asyncio.wait_for(asyncio.shield(second), 0.5)
        if peer_finishes:
            assert (
                next(row for row in sent if row["type"] == "http.response.start")["status"] == 200
            )
        await asyncio.wait_for(cancelled.wait(), 0.5)
        await asyncio.sleep(0)
        assert not reader._inflight
    finally:
        release.set()
        first.cancel()
        second.cancel()
        await asyncio.gather(first, second, return_exceptions=True)


@pytest.mark.asyncio
async def test_reader_close_is_bounded_refuses_admission_and_keeps_stubborn_slot():
    reader = HistoryDetailReader(None, algorithm_version="test-v1")
    entered, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def work(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
        reader._remember(("late",), "{}")
        return {"availability": "available"}

    reader._read = work
    task = asyncio.create_task(reader.read("race", drivers=[4], families=["laps"]))
    try:
        await entered.wait()
        start = time.monotonic()
        await asyncio.wait_for(reader.close(), 0.5)
        assert time.monotonic() - start < 0.5
        assert cancelled.is_set()
        assert len(reader._inflight) == 1
        with pytest.raises(DetailBusyError):
            await reader.read("race", drivers=[4], families=["laps"])
        untouched = SimpleNamespace(
            load=AsyncMock(side_effect=AssertionError("closed reader queried"))
        )
        with pytest.raises(DetailBusyError):
            await reader.read_session(
                "race", drivers=[4], families=["laps"], states=None, progress=untouched
            )
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
    assert not reader._inflight
    assert not reader._cache and reader._cache_bytes == 0


@pytest.mark.asyncio
async def test_container_closes_reader_before_disposing_dependencies(settings):
    from app.services.container import AppServices

    services = AppServices(settings)
    closed = []

    async def history_close():
        closed.append("history")

    services.history_details.close = history_close
    for name in ["database", "redis", "jolpica", "openf1", "openf1_auth"]:
        original = getattr(services, name).close

        async def close(original=original, name=name):
            assert closed == ["history"], name
            await original()

        getattr(services, name).close = close
    await services.close()
    assert closed == ["history"]
