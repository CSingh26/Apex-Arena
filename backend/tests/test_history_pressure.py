# SPDX-License-Identifier: AGPL-3.0-only
import asyncio
import json
from types import SimpleNamespace

import pytest

from app.providers.openf1 import OpenF1AuthService, OpenF1LiveClient
from app.services.history_ownership import HistoryContextBusyError
from tests.test_live_ingestion import START, runtime
from tests.test_openf1 import FakeProcessor


@pytest.mark.asyncio
async def test_mqtt_callback_admission_is_bounded_before_event_loop_dispatch(settings):
    auth = OpenF1AuthService(settings)
    live = OpenF1LiveClient(settings, auth, processor=FakeProcessor())
    live._loop = asyncio.get_running_loop()
    message = SimpleNamespace(topic="v1/laps", payload=b'{"session_key":901}')
    try:
        # No yield: all callbacks arrive before a coroutine can consume anything.
        for _ in range(300):
            live._on_message(None, None, message)
        status = live.status()
        assert status["dispatch_retained_rows"] == 256
        assert status["dispatch_overflow_rows"] == 44
        assert status["dispatch_gap_detected"] is True
        assert status["dispatch_retained_bytes"] <= 8 * 1024 * 1024
    finally:
        await live.disconnect()
        await auth.close()


@pytest.mark.asyncio
async def test_mqtt_pressure_retains_same_message_and_arrival_order(settings):
    class Pressured(FakeProcessor):
        def __init__(self):
            super().__init__()
            self.attempts = []
            self.done = asyncio.Event()

        async def ingest(self, raw):
            self.attempts.append(raw)
            if len(self.attempts) == 1:
                raise HistoryContextBusyError("busy")
            await super().ingest(raw)
            if len(self.events) == 2:
                self.done.set()

    settings.event_ordering_buffer_ms = 0
    auth = OpenF1AuthService(settings)
    processor = Pressured()
    live = OpenF1LiveClient(settings, auth, processor=processor)
    live._loop = asyncio.get_running_loop()
    try:
        for position in (1, 2):
            live._on_message(
                None,
                None,
                SimpleNamespace(
                    topic="v1/position",
                    payload=json.dumps({"session_key": 901, "position": position}).encode(),
                ),
            )
        await asyncio.wait_for(processor.done.wait(), 2)
        assert [row.raw_payload["position"] for row in processor.events] == [1, 2]
        assert processor.attempts[0] is processor.attempts[1]
        assert live.status()["dispatch_pressure_retries"] == 1
        assert live.status()["dispatch_unpersisted_rows"] == 0
    finally:
        await live.disconnect()
        await auth.close()


@pytest.mark.asyncio
async def test_rest_pressure_retries_same_batch_without_refetch_or_cursor_advance(settings):
    r = await runtime(settings)
    original = r.service.processor.ingest_batch
    attempts = []

    async def pressured(rows):
        attempts.append(rows)
        if len(attempts) == 1:
            assert not r.service.sessions["901"].seen
            raise HistoryContextBusyError("busy")
        assert attempts[0] is rows
        return await original(rows)

    r.service.processor.ingest_batch = pressured
    await r.service.run_once(now=START)
    assert len(attempts) == 2
    assert len([endpoint for endpoint, _ in r.client.reads if endpoint == "position"]) == 1
    assert r.service.sessions["901"].seen
    assert r.service.sessions["901"].endpoints["position"].failures == 0


@pytest.mark.asyncio
async def test_healthy_mqtt_burst_has_one_ordering_delay_not_one_per_message(settings):
    settings.event_ordering_buffer_ms = 1500
    auth = OpenF1AuthService(settings)
    flushed = asyncio.Event()

    class ObservedProcessor(FakeProcessor):
        async def flush_session(self, key):
            await super().flush_session(key)
            flushed.set()

    processor = ObservedProcessor()
    live = OpenF1LiveClient(settings, auth, processor=processor)
    live._loop = asyncio.get_running_loop()
    try:
        for position in range(20):
            live._on_message(
                None,
                None,
                SimpleNamespace(
                    topic="v1/position",
                    payload=json.dumps({"session_key": 901, "position": position}).encode(),
                ),
            )
        async with asyncio.timeout(2.5):
            await flushed.wait()
        assert len(processor.events) == 20
        assert processor.flushed == ["901"]
        assert live.status()["dispatch_retained_rows"] == 0
    finally:
        await live.disconnect()
        await auth.close()


@pytest.mark.asyncio
async def test_mqtt_pressure_exhaustion_is_sticky_after_later_success(settings):
    from app.providers.openf1 import LiveConnectionState

    class Busy(FakeProcessor):
        attempts = 0

        async def ingest(self, row):
            self.attempts += 1
            if self.attempts <= 4:
                raise HistoryContextBusyError("busy")
            await super().ingest(row)

    settings.event_ordering_buffer_ms = 0
    auth = OpenF1AuthService(settings)
    processor = Busy()
    live = OpenF1LiveClient(settings, auth, processor=processor)
    try:
        await live._handle_message("v1/laps", {"session_key": 901, "lap_number": 1})
        assert processor.attempts == 4
        assert live.status()["dispatch_pressure_exhausted"] == 1
        await live._handle_message("v1/laps", {"session_key": 901, "lap_number": 2})
        assert live.connection_state is LiveConnectionState.CONNECTED
        assert live.status()["dispatch_gap_detected"] is True
        assert live.status()["dispatch_unpersisted_rows"] == 1
        assert [row.raw_payload["lap_number"] for row in processor.events] == [2]
    finally:
        await auth.close()


@pytest.mark.asyncio
async def test_mqtt_bytes_and_cancelled_inflight_cleanup_are_bounded(settings):
    entered = asyncio.Event()

    class Blocked(FakeProcessor):
        async def ingest(self, row):
            entered.set()
            await asyncio.Event().wait()

    auth = OpenF1AuthService(settings)
    live = OpenF1LiveClient(settings, auth, processor=Blocked())
    live._loop = asyncio.get_running_loop()
    encoded = json.dumps({"session_key": 901, "padding": "x" * 65000}).encode()
    try:
        for _ in range(200):
            live._on_message(None, None, SimpleNamespace(topic="v1/laps", payload=encoded))
        assert live.status()["dispatch_retained_rows"] < 256
        assert live.status()["dispatch_retained_bytes"] <= 8 * 1024 * 1024
        assert live.status()["dispatch_overflow_rows"] > 0
        await entered.wait()
        await live.disconnect()
        assert live._dispatch_task.done()
        assert live.status()["dispatch_retained_rows"] == 0
        assert live.status()["dispatch_retained_bytes"] == 0
        assert live.status()["dispatch_gap_detected"]
    finally:
        await live.disconnect()
        await auth.close()


@pytest.mark.asyncio
async def test_rest_exhaustion_keeps_markers_and_is_not_provider_failure(settings):
    r = await runtime(settings)
    calls = []

    async def busy(rows):
        calls.append(rows)
        raise HistoryContextBusyError("busy")

    r.service.processor.ingest_batch = busy
    await r.service.run_once(now=START)
    assert len(calls) == 4
    assert all(rows is calls[0] for rows in calls)
    assert not r.service.sessions["901"].seen
    assert r.service.sessions["901"].endpoints["position"].cursor is None
    assert r.service.sessions["901"].endpoints["position"].failures == 0
    assert r.service.status["connection_state"] == "PROCESSOR_BUSY"
