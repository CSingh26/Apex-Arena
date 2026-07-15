# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import json
from typing import Any

from redis.asyncio import Redis

from app.domain.models import NormalizedRaceEvent


class RedisStore:
    def __init__(self, redis_url: str) -> None:
        self.client: Redis = Redis.from_url(redis_url, decode_responses=True)

    async def health_check(self, timeout_seconds: float = 2.0) -> tuple[bool, str]:
        try:
            async with asyncio.timeout(timeout_seconds):
                healthy = await self.client.ping()
            return bool(healthy), "connected" if healthy else "unavailable"
        except Exception as exc:
            return False, f"unavailable ({type(exc).__name__})"

    async def close(self) -> None:
        await self.client.aclose()


class EventBus:
    """Small Redis Stream boundary that can grow into the Day 2 event pipeline."""

    def __init__(self, redis: Redis, stream_name: str = "apex:race-events") -> None:
        self.redis = redis
        self.stream_name = stream_name

    async def publish_event(self, event: NormalizedRaceEvent) -> str:
        return await self.redis.xadd(
            self.stream_name,
            {
                "event_id": str(event.id),
                "event_type": event.event_type.value,
                "occurred_at": event.occurred_at.isoformat(),
                "data": event.model_dump_json(),
            },
        )

    async def read_events(self, after_id: str = "0-0", count: int = 100) -> list[dict[str, Any]]:
        streams = await self.redis.xread({self.stream_name: after_id}, count=count)
        events: list[dict[str, Any]] = []
        for _, messages in streams:
            for stream_id, values in messages:
                data = json.loads(values["data"])
                events.append({"stream_id": stream_id, **data})
        return events
