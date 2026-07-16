# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio

from app.core.settings import Settings
from app.providers.jolpica import JolpicaClient
from app.providers.openf1 import OpenF1AuthService, OpenF1LiveClient, OpenF1RestClient
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


class AppServices:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database = Database(settings.async_database_url)
        self.redis = RedisStore(settings.redis_dsn)
        self.event_bus = EventBus(self.redis.client)
        self.jolpica = JolpicaClient(settings.jolpica_base_url)
        self.openf1 = OpenF1RestClient(settings)
        self.openf1_auth = OpenF1AuthService(settings)
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
        self.rooms = RaceRoomService(self.room_repository, self.season, settings.season_year)
        self.room_discussion = RaceRoomDiscussionEngine(
            self.room_repository,
            DiscussionTriggerEvaluator(),
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
        )

    async def close(self) -> None:
        await self.openf1_live.disconnect()
        await asyncio.gather(
            self.database.close(),
            self.redis.close(),
            self.jolpica.close(),
            self.openf1.close(),
            self.openf1_auth.close(),
        )
