# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from redis.asyncio import Redis

from app.domain.models import NormalizedRaceEvent
from app.domain.rooms import RoomMessage
from app.services.race_state import RaceState, RaceStateEngine

logger = logging.getLogger(__name__)


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


class RedisPublishError(RuntimeError):
    pass


class EventBus:
    """Redis Streams transport for normalized events, race state, and live status."""

    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def publish_event(self, event: NormalizedRaceEvent) -> str:
        return await self._publish(
            self.event_stream(event.session_key),
            {
                "kind": "event",
                "sequence_number": str(event.sequence_number),
                "data": event.model_dump_json(),
            },
            maxlen=2000,
        )

    async def publish_state(self, state: RaceState) -> str:
        return await self._publish(
            self.state_stream(state.session_key),
            {
                "kind": "state",
                "sequence_number": str(state.sequence_number),
                "data": state.model_dump_json(),
            },
            maxlen=500,
        )

    async def publish_connection_status(self, status: dict[str, Any]) -> str:
        return await self._publish(
            "apex:live:status",
            {
                "kind": "connection_status",
                "data": json.dumps(status, sort_keys=True, default=str),
            },
            maxlen=200,
        )

    async def publish_room_message(self, message: RoomMessage) -> str:
        return await self._publish(
            self.room_stream(str(message.room_id)),
            {
                "kind": "room_message",
                "sequence_number": str(message.sequence),
                "data": message.model_dump_json(),
            },
            maxlen=5000,
        )

    async def publish_room_state(self, room_id: str, state: dict[str, Any]) -> str:
        return await self._publish(
            self.room_stream(room_id),
            {"kind": "playback_state", "data": json.dumps(state, default=str)},
            maxlen=5000,
        )

    async def read_room_stream(
        self,
        room_id: str,
        after_id: str,
        *,
        count: int = 100,
        block_ms: int = 10000,
    ) -> list[dict[str, Any]]:
        response = await self.redis.xread(
            {self.room_stream(room_id): after_id}, count=count, block=block_ms
        )
        return self._decode_streams(response)

    async def read_events(
        self, session_key: str, after_id: str = "0-0", count: int = 100
    ) -> list[dict[str, Any]]:
        streams = await self.redis.xread(
            {self.event_stream(session_key): after_id},
            count=count,
        )
        return self._decode_streams(streams)

    async def read_session_streams(
        self,
        session_key: str,
        last_ids: dict[str, str] | None = None,
        *,
        count: int = 100,
        block_ms: int = 10000,
    ) -> list[dict[str, Any]]:
        streams = {
            self.event_stream(session_key): "$",
            self.state_stream(session_key): "$",
            "apex:live:status": "$",
        }
        if last_ids:
            streams.update(last_ids)
        response = await self.redis.xread(streams, count=count, block=block_ms)
        return self._decode_streams(response)

    @classmethod
    def event_stream(cls, session_key: str) -> str:
        return f"apex:events:{cls._safe_key(session_key)}"

    @classmethod
    def state_stream(cls, session_key: str) -> str:
        return f"apex:state:{cls._safe_key(session_key)}"

    @classmethod
    def room_stream(cls, room_id: str) -> str:
        return f"apex:rooms:{cls._safe_key(room_id)}"

    async def _publish(self, stream: str, values: dict[str, str], maxlen: int) -> str:
        try:
            return await self.redis.xadd(stream, values, maxlen=maxlen, approximate=True)
        except Exception as exc:
            logger.error("Redis publish failed stream=%s error=%s", stream, type(exc).__name__)
            raise RedisPublishError(f"Redis publish failed ({type(exc).__name__})") from exc

    @staticmethod
    def _decode_streams(streams: list[object]) -> list[dict[str, Any]]:
        decoded: list[dict[str, Any]] = []
        for stream_name, messages in streams:  # type: ignore[misc]
            for stream_id, values in messages:
                decoded.append(
                    {
                        "stream": stream_name,
                        "stream_id": stream_id,
                        "kind": values.get("kind", "event"),
                        "sequence_number": int(values.get("sequence_number", 0)),
                        "data": json.loads(values["data"]),
                    }
                )
        return decoded

    @staticmethod
    def _safe_key(session_key: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]", "_", session_key)[:100]


class RaceEventRedisPublisher:
    def __init__(self, event_bus: EventBus, state_engine: RaceStateEngine) -> None:
        self.event_bus = event_bus
        self.state_engine = state_engine

    async def consume(self, event: NormalizedRaceEvent) -> None:
        await self.event_bus.publish_event(event)
        state = await self.state_engine.get_state(event.session_key)
        await self.event_bus.publish_state(state)
