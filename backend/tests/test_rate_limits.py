# SPDX-License-Identifier: AGPL-3.0-only
import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from redis.asyncio import Redis

from app.api.rate_limits import request_category
from app.main import create_app
from app.services.container import AppServices
from app.services.rate_limits import RedisRateLimiter


@pytest.mark.parametrize("redis_ready", [False, True])
async def test_enabled_limiter_keeps_readiness_dependency_body_during_redis_outage(
    settings, redis_ready
):
    configured = settings.model_copy(update={"rate_limit_enabled": True})
    app = create_app(configured)
    app.state.services = SimpleNamespace(
        settings=configured,
        rate_limiter=SimpleNamespace(admit=AsyncMock(side_effect=ConnectionError("synthetic"))),
        database=SimpleNamespace(health_check=AsyncMock(return_value=(True, None))),
        redis=SimpleNamespace(health_check=AsyncMock(return_value=(redis_ready, "synthetic"))),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["dependencies"] == {
        "database": "ready",
        "redis": "ready" if redis_ready else "unavailable",
        "admission": "unavailable",
    }


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("POST", "/api/v1/race-rooms/replay-operator/verify", "auth"),
        ("POST", "/api/v1/race-rooms/sync", "maintenance"),
        ("POST", "/api/v1/race-rooms/race/generate", "maintenance"),
        ("POST", "/api/v1/race-rooms/race/replay", "mutation"),
        ("POST", "/unknown", "maintenance"),
        ("GET", "/api/v1/race-rooms/race/stream", "sse"),
        ("GET", "/api/v1/stream/sessions/race", "sse"),
        ("GET", "/health/ready", "health"),
        ("GET", "/api/v1/season/2026", "read"),
    ],
)
def test_request_classes_share_budgets_across_actual_route_shapes(method, path, expected):
    assert request_category(method, path) == expected


async def test_proxy_rejection_precedes_limiter_and_redis_failure_keeps_cors_and_liveness(settings):
    class Unavailable:
        calls = 0

        async def admit(self, category):
            self.calls += 1
            raise ConnectionError("synthetic Redis outage")

    limiter = Unavailable()
    configured = settings.model_copy(
        update={
            "app_env": "staging",
            "rate_limit_enabled": True,
            "apex_arena_proxy_token": SecretStr("synthetic-hop"),
        }
    )
    app = create_app(configured)
    app.state.services = SimpleNamespace(rate_limiter=limiter, settings=configured)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://testserver"
    ) as client:
        assert (await client.get("/openapi.json")).status_code == 403
        assert limiter.calls == 0
        response = await client.get(
            "/openapi.json",
            headers={
                "X-Apex-Proxy-Token": "synthetic-hop",
                "Origin": "http://localhost:3000",
            },
        )
        assert response.status_code == 503
        assert response.json()["code"] == "rate_limit_unavailable"
        assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:3000"
        assert response.headers["Retry-After"] == "5"
        assert (await client.get("/health/live")).status_code == 200
        assert limiter.calls == 1


@pytest.mark.skipif(
    os.getenv("LIVE_REPAIR_INTEGRATION") != "1", reason="requires designated disposable Redis"
)
async def test_aggregate_admission_cannot_be_evaded_with_forwarded_headers(settings):
    namespace = f"apex:limits:test:{uuid4().hex}"
    configured = settings.model_copy(
        update={
            "redis_url": SecretStr("redis://127.0.0.1:16379/0"),
            "rate_limit_enabled": True,
            "rate_limit_namespace": namespace,
            "rate_limit_read_burst": 2,
            "rate_limit_read_per_minute": 1,
        }
    )
    app = create_app(configured)
    services = AppServices(configured)
    app.state.services = services
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://testserver"
        ) as client:
            first = await client.get("/openapi.json", headers={"X-Forwarded-For": "192.0.2.1"})
            second = await client.get("/openapi.json", headers={"X-Forwarded-For": "192.0.2.2"})
            third = await client.get("/openapi.json", headers={"X-Forwarded-For": "192.0.2.3"})
            assert [first.status_code, second.status_code, third.status_code] == [200, 200, 429]
            assert int(third.headers["Retry-After"]) >= 1
            assert third.headers["Cache-Control"] == "no-store"
            assert (await client.get("/health/live")).status_code == 200
    finally:
        await services.redis.client.delete(f"{namespace}:read")
        await services.close()


@pytest.mark.skipif(
    os.getenv("LIVE_REPAIR_INTEGRATION") != "1", reason="requires designated disposable Redis"
)
async def test_two_api_instances_share_atomic_budgets_and_expiring_stream_slots(settings):
    namespace = f"apex:limits:test:{uuid4().hex}"
    configured = settings.model_copy(
        update={
            "rate_limit_namespace": namespace,
            "rate_limit_read_burst": 5,
            "rate_limit_read_per_minute": 1,
            "rate_limit_stream_capacity": 2,
        }
    )
    redis = Redis.from_url("redis://127.0.0.1:16379/0", decode_responses=True)
    first, second = RedisRateLimiter(redis, configured), RedisRateLimiter(redis, configured)
    try:
        decisions = await asyncio.gather(
            *[(first if index % 2 else second).admit("read") for index in range(20)]
        )
        assert sum(decision.allowed for decision in decisions) == 5
        assert await first.acquire_stream("a") is True
        assert await second.acquire_stream("b") is True
        assert await second.acquire_stream("c") is False
        await first.release_stream("a")
        assert await second.acquire_stream("c") is True
        assert await second.renew_stream("c") is True
        await redis.zadd(f"{namespace}:streams", {"c": 0})
        assert await first.renew_stream("c") is False
        assert await first.acquire_stream("d") is True
        assert 0 < await redis.ttl(f"{namespace}:streams") <= 60
    finally:
        await redis.delete(f"{namespace}:read", f"{namespace}:streams")
        await redis.aclose()
