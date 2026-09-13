# SPDX-License-Identifier: AGPL-3.0-only
import asyncio
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.rate_limits import RateLimitMiddleware
from app.services.rate_limits import Admission


class StreamLimiter:
    timeout = 0.02
    stream_ttl = 0.12

    def __init__(self):
        self.tokens = set()
        self.renewals = 0
        self.releases = 0
        self.full = False
        self.outage = False
        self.acquire_started = asyncio.Event()
        self.acquire_blocked = False
        self.release_blocked = False

    async def admit(self, category):
        return Admission(True, 0)

    async def acquire_stream(self, token):
        self.acquire_started.set()
        if self.full:
            return False
        self.tokens.add(token)
        if self.acquire_blocked:
            await asyncio.Event().wait()
        return True

    async def renew_stream(self, token):
        self.renewals += 1
        if self.outage:
            raise ConnectionError("synthetic outage")
        return token in self.tokens

    async def release_stream(self, token):
        self.releases += 1
        if self.release_blocked:
            await asyncio.Event().wait()
        self.tokens.discard(token)


def stream_harness(settings, limiter, app, send=None):
    incoming = asyncio.Queue()
    incoming.put_nowait({"type": "http.request", "body": b"", "more_body": False})
    frames = []

    async def capture(message):
        frames.append(message)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/race-rooms/race/stream",
        "app": SimpleNamespace(
            state=SimpleNamespace(services=SimpleNamespace(rate_limiter=limiter))
        ),
    }
    middleware = RateLimitMiddleware(app, settings.model_copy(update={"rate_limit_enabled": True}))
    return middleware(scope, incoming.get, send or capture), incoming, frames


async def test_stream_capacity_is_acquired_before_response_or_route(settings):
    limiter = StreamLimiter()
    limiter.full = True
    entered = False

    async def app(scope, receive, send):
        nonlocal entered
        entered = True

    call, _, frames = stream_harness(settings, limiter, app)
    await call
    assert not entered
    assert frames[0]["status"] == 429
    assert (b"retry-after", b"1") in frames[0]["headers"]


@pytest.mark.parametrize("ending", ["normal", "disconnect", "cancel", "send_error"])
async def test_stream_slot_covers_body_and_is_released_on_every_exit(settings, ending):
    limiter = StreamLimiter()
    started, stopped, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def send(message):
        if ending == "send_error" and message["type"] == "http.response.body":
            raise OSError("synthetic disconnected socket")

    async def app(scope, receive, send):
        assert limiter.tokens
        try:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            started.set()
            await send({"type": "http.response.body", "body": b"data: ok\n\n", "more_body": True})
            await finish.wait()
        finally:
            stopped.set()

    call, incoming, _ = stream_harness(settings, limiter, app, send)
    task = asyncio.create_task(call)
    if ending == "send_error":
        with pytest.raises(OSError):
            await task
    else:
        await asyncio.wait_for(started.wait(), 0.5)
        await asyncio.sleep(0.06)
        assert limiter.tokens and limiter.renewals >= 1
        if ending == "normal":
            finish.set()
        elif ending == "disconnect":
            incoming.put_nowait({"type": "http.disconnect"})
        else:
            task.cancel()
        if ending == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await asyncio.wait_for(task, 0.5)
    assert stopped.is_set()
    assert not limiter.tokens
    assert limiter.releases == 1


@pytest.mark.parametrize("failure", ["lost", "outage", "disconnect"])
async def test_blocked_send_is_cancelled_by_lease_loss_expiry_or_disconnect(settings, failure):
    limiter = StreamLimiter()
    sending, stopped = asyncio.Event(), asyncio.Event()

    async def send(message):
        sending.set()
        await asyncio.Event().wait()

    async def app(scope, receive, send):
        try:
            await send({"type": "http.response.start", "status": 200, "headers": []})
        finally:
            stopped.set()

    call, incoming, _ = stream_harness(settings, limiter, app, send)
    task = asyncio.create_task(call)
    await asyncio.wait_for(sending.wait(), 0.5)
    if failure == "lost":
        limiter.tokens.clear()
    elif failure == "outage":
        limiter.outage = True
    else:
        incoming.put_nowait({"type": "http.disconnect"})
    await asyncio.wait_for(task, 0.5)
    assert stopped.is_set()
    assert limiter.releases == 1
    assert not limiter.tokens


@pytest.mark.parametrize("cancel", [False, True])
async def test_uncertain_acquisition_is_bounded_and_cleaned_before_app_start(settings, cancel):
    limiter = StreamLimiter()
    limiter.acquire_blocked = True
    entered = False

    async def app(scope, receive, send):
        nonlocal entered
        entered = True

    call, _, frames = stream_harness(settings, limiter, app)
    task = asyncio.create_task(call)
    await asyncio.wait_for(limiter.acquire_started.wait(), 0.5)
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        await asyncio.wait_for(task, 0.5)
        assert frames[0]["status"] == 503
    assert not entered
    assert not limiter.tokens
    assert limiter.releases == 1


async def test_release_outage_is_bounded_and_leaves_only_expiring_token(settings):
    limiter = StreamLimiter()
    limiter.release_blocked = True

    async def app(scope, receive, send):
        pass

    call, _, _ = stream_harness(settings, limiter, app)
    await asyncio.wait_for(call, 0.2)
    assert limiter.releases == 1


async def test_slow_response_finalizer_cannot_block_disconnect_cleanup(settings):
    limiter = StreamLimiter()
    started = asyncio.Event()

    async def app(scope, receive, send):
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            await asyncio.Event().wait()

    call, incoming, _ = stream_harness(settings, limiter, app)
    task = asyncio.create_task(call)
    await started.wait()
    incoming.put_nowait({"type": "http.disconnect"})
    await asyncio.wait_for(task, 0.2)


async def test_redis_stream_command_has_its_own_deadline(settings):
    from app.services.rate_limits import RedisRateLimiter

    class BlockedRedis:
        async def eval(self, *args):
            await asyncio.Event().wait()

    limiter = RedisRateLimiter(
        BlockedRedis(), settings.model_copy(update={"rate_limit_timeout_seconds": 0.01})
    )
    for command in (limiter.acquire_stream, limiter.renew_stream, limiter.release_stream):
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(command("synthetic-token"), 0.1)


@pytest.mark.parametrize("timeout,ttl", [(1, 3), (2, 3)])
def test_settings_reserve_renewal_margin(settings, timeout, ttl):
    from pydantic import ValidationError

    from app.core.settings import Settings

    with pytest.raises(ValidationError, match="renewal"):
        Settings(
            **{
                **settings.model_dump(),
                "rate_limit_timeout_seconds": timeout,
                "rate_limit_stream_ttl_seconds": ttl,
            }
        )


async def test_actual_streaming_response_finalizes_generator_and_reader_on_disconnect(settings):
    from starlette.responses import StreamingResponse

    limiter = StreamLimiter()
    reading, finalized = asyncio.Event(), asyncio.Event()

    async def events():
        try:
            yield b"data: synthetic\n\n"
            reading.set()
            await asyncio.Event().wait()
        finally:
            finalized.set()

    response = StreamingResponse(events(), media_type="text/event-stream")
    before = asyncio.all_tasks()
    call, incoming, frames = stream_harness(settings, limiter, response)
    task = asyncio.create_task(call)
    await asyncio.wait_for(reading.wait(), 0.5)
    assert frames[0]["status"] == 200
    incoming.put_nowait({"type": "http.disconnect"})
    await asyncio.wait_for(task, 0.5)
    assert finalized.is_set()
    assert not limiter.tokens
    assert not (asyncio.all_tasks() - before)


async def test_cancellation_while_application_has_not_sent_headers_releases_slot(settings):
    limiter = StreamLimiter()
    entered, finalized = asyncio.Event(), asyncio.Event()

    async def app(scope, receive, send):
        try:
            entered.set()
            await asyncio.Event().wait()
        finally:
            finalized.set()

    call, _, frames = stream_harness(settings, limiter, app)
    task = asyncio.create_task(call)
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not frames and finalized.is_set() and not limiter.tokens


@pytest.mark.skipif(
    os.getenv("LIVE_REPAIR_INTEGRATION") != "1", reason="requires designated disposable Redis"
)
async def test_real_redis_middleware_instances_share_slots_and_cancel_expired_stream(settings):
    from redis.asyncio import Redis

    from app.services.rate_limits import RedisRateLimiter

    namespace = f"apex:limits:test:{uuid4().hex}"
    redis = Redis.from_url("redis://127.0.0.1:16379/0", decode_responses=True)
    configured = settings.model_copy(
        update={
            "rate_limit_namespace": namespace,
            "rate_limit_stream_capacity": 1,
            "rate_limit_stream_ttl_seconds": 3,
        }
    )
    first, second = RedisRateLimiter(redis, configured), RedisRateLimiter(redis, configured)
    entered, finalized = asyncio.Event(), asyncio.Event()

    async def app(scope, receive, send):
        try:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            entered.set()
            await asyncio.Event().wait()
        finally:
            finalized.set()

    call, _, _ = stream_harness(configured, first, app)
    task = asyncio.create_task(call)
    try:
        await asyncio.wait_for(entered.wait(), 1)
        denied, _, frames = stream_harness(configured, second, app)
        await denied
        assert frames[0]["status"] == 429
        assert await redis.zcard(f"{namespace}:streams") == 1
        tokens = await redis.zrange(f"{namespace}:streams", 0, -1)
        await redis.zadd(f"{namespace}:streams", {tokens[0]: 0})
        await asyncio.wait_for(task, 2)
        assert finalized.is_set()
        assert await redis.zcard(f"{namespace}:streams") == 0
        assert await second.acquire_stream("replacement")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await redis.delete(f"{namespace}:sse", f"{namespace}:streams")
        await redis.aclose()
