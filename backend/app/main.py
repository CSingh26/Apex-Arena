# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.room_routes import router as room_router
from app.api.routes import router
from app.core.logging import configure_logging
from app.core.settings import Settings, get_settings
from app.services.container import AppServices


def create_app(settings_override: Settings | None = None) -> FastAPI:
    settings = settings_override or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings)
        application.state.services = AppServices(settings)
        if settings.openf1_live_auto_connect:
            await application.state.services.openf1_live.connect()
        try:
            yield
        finally:
            await application.state.services.close()

    application = FastAPI(
        title="Apex Arena API",
        version="0.1.0",
        description="Day 2 unified live and replay race engine for the 2026 season.",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Accept", "Content-Type", "X-Internal-API-Key"],
    )
    application.include_router(router)
    application.include_router(room_router)
    return application


app = create_app()
