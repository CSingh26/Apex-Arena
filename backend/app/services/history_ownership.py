# SPDX-License-Identifier: AGPL-3.0-only
"""Local private-context lifetime, not distributed source ownership."""

import asyncio
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass


class HistoryContextBusyError(RuntimeError):
    pass


@dataclass
class ContextPin:
    pool: "HistoryContextPool"
    session_key: str
    released: bool = False

    def release(self):
        if not self.released:
            self.released = True
            self.pool._pins[self.session_key] -= 1


class HistoryContextPool:
    def __init__(self, capacity: int, evict):
        self.capacity = capacity
        self.evict = evict
        self._pins: OrderedDict[str, int] = OrderedDict()
        self._lock = asyncio.Lock()

    async def acquire(self, session_key: str) -> ContextPin:
        async with self._lock:
            if session_key not in self._pins:
                if len(self._pins) >= self.capacity:
                    victim = next((key for key, pins in self._pins.items() if pins == 0), None)
                    if victim is None:
                        raise HistoryContextBusyError(
                            "Private history context pool is busy; retry shortly"
                        )
                    # Admission excludes new pins while the idle owner's anchor,
                    # detector state and private history are invalidated together.
                    await self.evict(victim)
                    self._pins.pop(victim)
                self._pins[session_key] = 0
            self._pins[session_key] += 1
            self._pins.move_to_end(session_key)
            return ContextPin(self, session_key)

    @asynccontextmanager
    async def pin(self, session_key: str):
        token = await self.acquire(session_key)
        try:
            yield
        finally:
            # Non-awaiting release cannot strand a pin on repeated cancellation.
            token.release()
