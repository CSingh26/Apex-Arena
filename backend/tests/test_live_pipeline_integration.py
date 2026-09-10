# SPDX-License-Identifier: AGPL-3.0-only
"""Opt-in real SQL/Redis test; fixtures never enter application environments.

LIVE_REPAIR_INTEGRATION=1 uses disposable test services on ports 55433/16379.
"""

import os
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.api.streaming import session_event_stream
from app.core.settings import Settings
from app.services.container import AppServices
from app.services.race_state import RaceStateEngine
from app.storage.database import Base, Database
from tests.test_live_ingestion import LiveProvider
from tests.test_live_room_repair import START, monza, provider
from tests.test_race_rooms_service import FakeSeason

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("LIVE_REPAIR_INTEGRATION") != "1", reason="requires isolated SQL and Redis"
    ),
]


async def test_real_storage_live_fanout_restart_and_completion():
    settings = Settings(
        _env_file=None,
        app_env="test",
        app_process_role="combined",
        season_year=2026,
        database_url="postgresql://apex_test:repair_test_only@127.0.0.1:55433/apex_live_repair",
        postgres_password="repair_test_only",
        redis_url="redis://127.0.0.1:16379/0",
        openf1_ingestion_mode="rest",
        openf1_mqtt_autoconnect=False,
    )
    services = AppServices(settings)
    other = Database(settings.async_process_database_url)
    try:
        async with services.database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
        await services.redis.client.flushdb()
        assert await services.database.acquire_ingestor_lease()
        assert not await other.acquire_ingestor_lease()
        client = LiveProvider()
        services.rooms.season = FakeSeason([monza()])
        services.rooms.openf1 = client
        services.live_ingestion.client = client
        # The normalized stream still drives real state, Redis and discussion.
        services.processor.consumers.remove(services.championship)
        await services.live_ingestion.run_once(now=START)
        room = await services.room_repository.get_room_by_session("901")
        assert room is not None and room.status.value == "live"
        count = await services.normalized_event_repository.count("901")
        assert count >= 4
        assert await services.location_repository.count("901") == 1
        assert await services.redis.client.xlen(services.event_bus.event_stream("901")) >= 4
        shared = await services.event_bus.latest_state("901")
        assert shared.drivers["16"].location == {"x": 100.0, "y": 200.0, "z": 1.0}
        reader = RaceStateEngine(
            services.snapshot_repository, live_state_reader=services.event_bus.latest_state
        )
        assert (await reader.get_state("901")).sequence_number == shared.sequence_number
        assert await services.room_repository.list_messages(room.id, limit=100)

        # Two independent viewers use the same persisted events and shared feed.
        async def connected():
            return False

        request = SimpleNamespace(is_disconnected=connected)
        viewers = [session_event_stream(request, services, "901", 0) for _ in range(2)]
        try:
            for viewer in viewers:
                frames = [await anext(viewer) for _ in range(count + 1)]
                assert any("LOCATION_SAMPLE" in frame for frame in frames)
            assert services.event_bus.diagnostics("901")["active_session_sse_clients"] == 2
        finally:
            for viewer in viewers:
                await viewer.aclose()
        assert services.event_bus.diagnostics("901")["active_session_sse_clients"] == 0
        await services.live_ingestion.run_once(now=START + timedelta(seconds=15))
        assert await services.normalized_event_repository.count("901") == count
        client.session_rows = [{**provider(), "status": "Finished"}]
        await services.live_ingestion.run_once(now=START + timedelta(seconds=61))
        archived = await services.room_repository.get_room_by_session("901")
        assert archived.id == room.id
        assert archived.status.value == "completed" and archived.mode.value == "archived"
        assert await services.normalized_event_repository.count("901") >= count
        candidates = await services.room_repository.list_recent_reconciliation_candidates(
            now=START + timedelta(hours=5),
            lookback_days=3,
            grace_minutes=15,
            limit=100,
        )
        assert any(candidate.id == room.id for candidate in candidates)
        # A database failure after raw commit must remain recoverable on retry.
        from unittest.mock import AsyncMock

        from app.services.raw_events import RawEventInput

        services.processor.consumers = []
        raw = RawEventInput(
            provider_endpoint="position",
            session_key="retry-test",
            raw_payload={"driver_number": 16, "position": 1},
            event_time=START,
        )
        original_insert = services.normalized_event_repository.insert
        services.normalized_event_repository.insert = AsyncMock(
            side_effect=RuntimeError("storage failure")
        )
        with pytest.raises(RuntimeError):
            await services.processor.ingest_batch([raw])
        services.normalized_event_repository.insert = original_insert
        await services.processor.ingest_batch([raw])
        assert await services.raw_event_repository.count("retry-test") == 1
        assert await services.normalized_event_repository.count("retry-test") == 1
    finally:
        await other.close()
        await services.close()
