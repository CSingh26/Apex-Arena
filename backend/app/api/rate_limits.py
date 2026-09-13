# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import math
from uuid import uuid4

import anyio
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.settings import Settings
from app.services.rate_limits import RedisRateLimiter


def request_category(method: str, path: str) -> str:
    if method in {"GET", "HEAD"}:
        if path.startswith("/health"):
            return "health"
        if path.startswith("/api/v1/stream/sessions/") or (
            path.startswith("/api/v1/race-rooms/") and path.endswith("/stream")
        ):
            return "sse"
        return "read"
    if path == "/api/v1/race-rooms/replay-operator/verify":
        return "auth"
    if path == "/api/v1/race-rooms/sync" or path.endswith("/generate"):
        return "maintenance"
    if path.startswith("/api/v1/race-rooms/"):
        return "mutation"
    return "maintenance"


class RateLimitMiddleware:
    """Own admission inside proxy authentication and outside route execution."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.enabled = settings.rate_limit_enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            not self.enabled
            or scope["type"] != "http"
            or scope["path"] == "/health/live"
            or scope["method"] == "OPTIONS"
        ):
            await self.app(scope, receive, send)
            return
        category = request_category(scope["method"], scope["path"])
        try:
            limiter = scope["app"].state.services.rate_limiter
            decision = await limiter.admit(category)
        except Exception:
            if scope["path"] == "/health/ready" and scope["method"] in {"GET", "HEAD"}:
                # Preserve bounded dependency diagnostics without admitting
                # business work; readiness must still fail if EVAL is down.
                scope.setdefault("state", {})["admission_unavailable"] = True
                await self.app(scope, receive, send)
                return
            response = JSONResponse(
                {
                    "code": "rate_limit_unavailable",
                    "detail": "Request admission is temporarily unavailable",
                },
                status_code=503,
                headers={"Retry-After": "5", "Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
            return
        if not decision.allowed:
            response = JSONResponse(
                {"code": "rate_limit_exceeded", "detail": "Request limit reached; retry shortly"},
                status_code=429,
                headers={"Retry-After": str(decision.retry_after), "Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
            return
        if category == "sse":
            await self._stream(scope, receive, send, limiter)
        else:
            await self.app(scope, receive, send)

    async def _stream(
        self, scope: Scope, receive: Receive, send: Send, limiter: RedisRateLimiter
    ) -> None:
        # Unique ownership also permits cleanup after an ambiguous Redis timeout.
        token = uuid4().hex
        tasks: list[asyncio.Task] = []
        loop = asyncio.get_running_loop()
        deadline = loop.time() + limiter.stream_ttl
        started = False
        release_safe = True
        try:
            try:
                async with asyncio.timeout(limiter.timeout):
                    acquired = await limiter.acquire_stream(token)
            except Exception:
                await self._stream_error(scope, receive, send, 503, 5, limiter.timeout)
                return
            if not acquired:
                await self._stream_error(
                    scope,
                    receive,
                    send,
                    429,
                    max(1, math.ceil(limiter.stream_ttl)),
                    limiter.timeout,
                )
                return

            # One receive owner observes disconnect even if the application is
            # blocked sending. These GET streams do not accept request bodies;
            # bounded buffering avoids an unconsumed upload retaining memory.
            incoming: asyncio.Queue = asyncio.Queue(maxsize=1)

            async def watch_disconnect() -> None:
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    try:
                        incoming.put_nowait(message)
                    except asyncio.QueueFull:
                        return

            async def fenced_send(message) -> None:
                nonlocal started
                if loop.time() >= deadline:
                    raise asyncio.CancelledError("stream lease expired")
                if message["type"] == "http.response.start":
                    # Mark before awaiting: a blocked send may have sent headers.
                    started = True
                await send(message)

            async def renew() -> None:
                nonlocal deadline
                while True:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        return
                    await asyncio.sleep(min(limiter.stream_ttl / 3, remaining))
                    began = loop.time()
                    remaining = deadline - began
                    if remaining <= 0:
                        return
                    try:
                        async with asyncio.timeout(min(limiter.timeout, remaining)):
                            owned = await limiter.renew_stream(token)
                        if not owned:
                            return
                        # Conservative local fence: never count network latency
                        # after Redis applied the TTL as additional lease time.
                        deadline = began + limiter.stream_ttl
                    except Exception:
                        # A temporary outage is retryable only inside the last
                        # confirmed lease. It never extends stream authority.
                        continue

            application = asyncio.create_task(self.app(scope, incoming.get, fenced_send))
            disconnected = asyncio.create_task(watch_disconnect())
            renewal = asyncio.create_task(renew())
            tasks = [application, disconnected, renewal]
            completed, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if application in completed:
                await application
            elif renewal in completed and not started:
                application.cancel()
                await asyncio.wait([application], timeout=limiter.timeout)
                # No route headers have been sent, so an actionable failure is
                # still possible. Once started, cancellation closes the stream.
                if application.done() and not started:
                    await self._stream_error(scope, receive, send, 503, 5, limiter.timeout)
        finally:
            # Shield framework cancellation long enough for cooperative response
            # generators to finalize and a deadline-bounded token release. Redis
            # TTL remains the fallback after process death or cleanup outage.
            with anyio.CancelScope(shield=True):
                for task in tasks:
                    task.cancel()
                if tasks:
                    _, pending = await asyncio.wait(tasks, timeout=limiter.timeout)
                    for task in tasks:
                        if task in pending:
                            task.cancel()
                            task.add_done_callback(self._consume_task_result)
                        else:
                            self._consume_task_result(task)
                    # Do not free capacity while a non-cooperative application
                    # finalizer is still alive. Its unrenewed lease expires.
                    if application in pending:
                        release_safe = False
                if release_safe:
                    try:
                        async with asyncio.timeout(limiter.timeout):
                            await limiter.release_stream(token)
                    except Exception:
                        pass

    @staticmethod
    def _consume_task_result(task: asyncio.Task) -> None:
        if not task.cancelled():
            task.exception()

    @staticmethod
    async def _stream_error(
        scope, receive, send, status: int, retry: int, deadline_seconds: float
    ) -> None:
        unavailable = status == 503
        response = JSONResponse(
            {
                "code": "rate_limit_unavailable" if unavailable else "rate_limit_exceeded",
                "detail": (
                    "Stream admission is temporarily unavailable; retry shortly"
                    if unavailable
                    else "Stream capacity reached; retry shortly"
                ),
            },
            status_code=status,
            headers={"Retry-After": str(retry), "Cache-Control": "no-store"},
        )
        try:
            async with asyncio.timeout(deadline_seconds):
                await response(scope, receive, send)
        except TimeoutError:
            pass  # A blocked peer cannot retain an ambiguous acquisition.
