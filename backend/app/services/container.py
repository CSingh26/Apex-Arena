# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from app.core.settings import Settings
from app.providers.jolpica import JolpicaClient
from app.providers.openf1 import OpenF1AuthService, OpenF1LiveClient, OpenF1RestClient
from app.services.circuit_intelligence import (
    CircuitIntelligenceService,
    CircuitWeatherService,
)
from app.services.development_fixture import DevelopmentFixtureService
from app.services.discussion import RaceRoomDiscussionEngine
from app.services.discussion_triggers import DiscussionTriggerEvaluator
from app.services.event_pipeline import (
    EventDeduplicator,
    EventOrderingBuffer,
    RaceEventProcessor,
    SequenceNumberService,
)
from app.services.historical import HistoricalOpenF1Adapter
from app.services.normalization import OpenF1EventNormalizer
from app.services.race_state import RaceStateEngine
from app.services.raw_events import RawProviderEventService
from app.services.room_eligibility import RoomEligibilityService
from app.services.room_replay import RoomReplayCoordinator
from app.services.rooms import RaceRoomService
from app.services.season import SeasonService
from app.storage.database import Database
from app.storage.redis import EventBus, RaceEventRedisPublisher, RedisStore
from app.storage.repositories import (
    SqlIngestionRunRepository,
    SqlNormalizedEventRepository,
    SqlRaceStateSnapshotRepository,
    SqlRawEventRepository,
)
from app.storage.room_repository import SqlRaceRoomRepository

logger = logging.getLogger(__name__)


class AppServices:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # The ingestor holds a session-scoped advisory lease, which a transaction
        # pooler would silently break. Combined mode needs the same direct DSN
        # whenever it is ingesting; API-only processes keep the pooled runtime DSN.
        self.database = Database(
            settings.async_process_database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout_seconds,
            pool_recycle=settings.db_pool_recycle_seconds,
        )
        self.redis = RedisStore(
            settings.redis_dsn,
            socket_timeout=settings.effective_redis_socket_timeout,
            connect_timeout=settings.redis_connect_timeout_seconds,
            health_check_interval=settings.redis_health_check_interval_seconds,
        )
        self.event_bus = EventBus(self.redis.client)
        self.jolpica = JolpicaClient(settings.jolpica_base_url)
        self.openf1_auth = OpenF1AuthService(settings)
        self.openf1 = OpenF1RestClient(
            settings,
            token_provider=self.openf1_auth.get_access_token,
        )
        self.circuit_intelligence = CircuitIntelligenceService()
        self.circuit_weather = CircuitWeatherService(self.openf1)
        self.season = SeasonService(settings, self.jolpica)

        self.raw_event_repository = SqlRawEventRepository(self.database)
        self.normalized_event_repository = SqlNormalizedEventRepository(self.database)
        self.snapshot_repository = SqlRaceStateSnapshotRepository(self.database)
        self.ingestion_runs = SqlIngestionRunRepository(self.database)
        self.room_repository = SqlRaceRoomRepository(self.database)
        self.raw_events = RawProviderEventService(self.raw_event_repository)
        self.ordering_buffer = EventOrderingBuffer(settings.event_ordering_buffer_ms)
        self.race_state = RaceStateEngine(
            self.snapshot_repository,
            settings.race_state_snapshot_every_n_events,
        )
        self.redis_publisher = RaceEventRedisPublisher(self.event_bus, self.race_state)
        fixture = (
            DevelopmentFixtureService(self.normalized_event_repository)
            if settings.app_env in {"local", "test"} and settings.development_fixture_enabled
            else None
        )
        self.room_eligibility = RoomEligibilityService()
        self.rooms = RaceRoomService(
            self.room_repository,
            self.season,
            settings.season_year,
            fixture=fixture,
            openf1=self.openf1,
            eligibility=self.room_eligibility,
        )
        self.room_discussion = RaceRoomDiscussionEngine(
            self.room_repository,
            DiscussionTriggerEvaluator(settings.room_topic_cooldown_seconds),
            publisher=self.event_bus.publish_room_message,
            state_reader=self.race_state.get_state,
        )
        self.room_replay = RoomReplayCoordinator(
            self.room_repository,
            self.normalized_event_repository,
            self.room_discussion,
            self.race_state,
            self.event_bus,
            settings.room_replay_interval_seconds,
        )
        self.processor = RaceEventProcessor(
            raw_events=self.raw_events,
            normalizer=OpenF1EventNormalizer(),
            normalized_repository=self.normalized_event_repository,
            deduplicator=EventDeduplicator(settings.event_dedup_ttl_seconds),
            ordering_buffer=self.ordering_buffer,
            sequence_numbers=SequenceNumberService(self.normalized_event_repository),
            consumers=[self.race_state, self.redis_publisher, self.room_discussion],
        )
        self.openf1_live = OpenF1LiveClient(
            settings,
            self.openf1_auth,
            processor=self.processor,
            event_bus=self.event_bus,
        )
        self.historical = HistoricalOpenF1Adapter(
            client=self.openf1,
            processor=self.processor,
            runs=self.ingestion_runs,
            snapshots=self.snapshot_repository,
            max_records_per_endpoint=settings.historical_ingestion_max_records_per_endpoint,
            room_availability=self.room_repository,
        )
        self._live_catalog_task: asyncio.Task[None] | None = None

    async def start_live_services(self) -> None:
        """Connect live telemetry and reconcile provider sessions in the background."""
        await self.openf1_live.connect()
        if self._live_catalog_task is None or self._live_catalog_task.done():
            self._live_catalog_task = asyncio.create_task(
                self._maintain_live_catalog(), name="openf1-live-catalog"
            )

    async def _maintain_live_catalog(self) -> None:
        while True:
            try:
                await self.rooms.force_sync()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Live room catalog refresh failed error=%s", type(exc).__name__)
            await asyncio.sleep(self.settings.openf1_live_catalog_sync_seconds)

    async def close(self) -> None:
        if self._live_catalog_task is not None:
            self._live_catalog_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._live_catalog_task
            self._live_catalog_task = None
        await self.openf1_live.disconnect()
        await self.room_replay.close()
        await asyncio.gather(
            self.database.close(),
            self.redis.close(),
            self.jolpica.close(),
            self.openf1.close(),
            self.openf1_auth.close(),
        )
