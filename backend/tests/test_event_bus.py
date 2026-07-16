# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.domain.models import NormalizedRaceEvent, RaceEventType
from app.services.race_state import RaceStateEngine, SnapshotPersistResult
from app.storage.redis import EventBus, RaceEventRedisPublisher, RedisPublishError


class FakeRedis:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.entries: list[tuple[str, dict[str, str]]] = []

    async def xadd(
        self,
        stream: str,
        values: dict[str, str],
        *,
        maxlen: int,
        approximate: bool,
    ) -> str:
        if self.fail:
            raise ConnectionError("private connection details")
        self.entries.append((stream, values))
        return f"1-{len(self.entries)}"

    async def xread(self, streams: dict[str, str], **_: Any) -> list[object]:
        return []


class Snapshots:
    async def insert(self, snapshot: object) -> SnapshotPersistResult:
        return SnapshotPersistResult(record_id=snapshot.id, is_new=True)  # type: ignore[attr-defined]

    async def latest(self, session_key: str) -> None:
        return None

    async def count(self, session_key: str | None = None) -> int:
        return 0


def race_event() -> NormalizedRaceEvent:
    now = datetime(2026, 7, 19, 13, tzinfo=UTC)
    return NormalizedRaceEvent(
        session_key="spa/race",
        source="openf1",
        event_time=now,
        received_at=now,
        sequence_number=1,
        event_type=RaceEventType.POSITION_SAMPLE,
        driver_numbers=[4],
        payload={"position": 1},
        dedup_key="position-one",
    )


@pytest.mark.asyncio
async def test_event_and_state_are_published_to_session_streams() -> None:
    redis = FakeRedis()
    bus = EventBus(redis)  # type: ignore[arg-type]
    state_engine = RaceStateEngine(Snapshots())  # type: ignore[arg-type]
    event = race_event()
    await state_engine.apply(event)
    publisher = RaceEventRedisPublisher(bus, state_engine)

    await publisher.consume(event)

    assert [entry[0] for entry in redis.entries] == [
        "apex:events:spa_race",
        "apex:state:spa_race",
    ]
    assert redis.entries[0][1]["sequence_number"] == "1"


@pytest.mark.asyncio
async def test_redis_publish_failure_is_explicit_and_secret_safe() -> None:
    bus = EventBus(FakeRedis(fail=True))  # type: ignore[arg-type]

    with pytest.raises(RedisPublishError, match="ConnectionError") as error:
        await bus.publish_event(race_event())

    assert "private connection details" not in str(error.value)
