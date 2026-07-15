# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio

from app.core.settings import Settings
from app.providers.jolpica import JolpicaClient
from app.providers.openf1 import OpenF1AuthService, OpenF1LiveClient, OpenF1RestClient
from app.services.season import SeasonService
from app.storage.database import Database
from app.storage.redis import EventBus, RedisStore


class AppServices:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database = Database(settings.async_database_url)
        self.redis = RedisStore(settings.redis_dsn)
        self.event_bus = EventBus(self.redis.client)
        self.jolpica = JolpicaClient(settings.jolpica_base_url)
        self.openf1 = OpenF1RestClient(settings)
        self.openf1_auth = OpenF1AuthService(settings)
        self.openf1_live = OpenF1LiveClient(settings, self.openf1_auth)
        self.season = SeasonService(settings, self.jolpica)

    async def close(self) -> None:
        await asyncio.gather(
            self.database.close(),
            self.redis.close(),
            self.jolpica.close(),
            self.openf1.close(),
            self.openf1_auth.close(),
        )
