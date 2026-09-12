# SPDX-License-Identifier: AGPL-3.0-only
"""Redis-owned aggregate admission budgets; forwarded identities are untrusted."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from redis.asyncio import Redis

from app.core.settings import Settings

_ADMIT = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2]) / 60
local state = redis.call('HMGET', KEYS[1], 'tokens', 'updated')
local tokens = tonumber(state[1]) or capacity
local updated = tonumber(state[2]) or now
tokens = math.min(capacity, tokens + math.max(0, now - updated) * rate)
local allowed = 0
local retry = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
else
  retry = math.max(1, math.ceil((1 - tokens) / rate))
end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'updated', now)
redis.call('EXPIRE', KEYS[1], math.ceil(capacity / rate) * 2 + 60)
return {allowed, retry}
"""

_STREAM = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
local operation = ARGV[1]
local token = ARGV[2]
local ttl = tonumber(ARGV[3])
if operation == 'release' then
  return redis.call('ZREM', KEYS[1], token)
end
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
local existing = redis.call('ZSCORE', KEYS[1], token)
if operation == 'renew' then
  if not existing then return 0 end
else
  if existing or redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[4]) then return 0 end
end
redis.call('ZADD', KEYS[1], now + ttl, token)
redis.call('PEXPIRE', KEYS[1], ttl * 2)
return 1
"""


@dataclass(frozen=True)
class Admission:
    allowed: bool
    retry_after: int


class RedisRateLimiter:
    def __init__(self, redis: Redis, settings: Settings) -> None:
        self.redis = redis
        self.namespace = settings.rate_limit_namespace or f"apex:limits:v1:{settings.app_env}"
        self.timeout = settings.rate_limit_timeout_seconds
        self.stream_ttl = settings.rate_limit_stream_ttl_seconds
        self.stream_capacity = settings.rate_limit_stream_capacity
        self.budgets = {
            "read": (settings.rate_limit_read_burst, settings.rate_limit_read_per_minute),
            "auth": (settings.rate_limit_auth_burst, settings.rate_limit_auth_per_minute),
            "mutation": (
                settings.rate_limit_mutation_burst,
                settings.rate_limit_mutation_per_minute,
            ),
            "maintenance": (1, 4),
            "sse": (settings.rate_limit_sse_burst, settings.rate_limit_sse_per_minute),
            "health": (10, 60),
        }

    async def admit(self, category: str) -> Admission:
        burst, rate = self.budgets[category]
        async with asyncio.timeout(self.timeout):
            allowed, retry_after = await self.redis.eval(
                _ADMIT, 1, f"{self.namespace}:{category}", burst, rate
            )
        return Admission(bool(allowed), int(retry_after))

    async def _stream(self, operation: str, token: str) -> bool:
        async with asyncio.timeout(self.timeout):
            result = await self.redis.eval(
                _STREAM,
                1,
                f"{self.namespace}:streams",
                operation,
                token,
                self.stream_ttl * 1000,
                self.stream_capacity,
            )
        return bool(result)

    async def acquire_stream(self, token: str) -> bool:
        return await self._stream("acquire", token)

    async def renew_stream(self, token: str) -> bool:
        return await self._stream("renew", token)

    async def release_stream(self, token: str) -> None:
        await self._stream("release", token)
