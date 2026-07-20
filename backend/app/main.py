# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.proxy import ProxyContextMiddleware
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
        services = AppServices(settings)
        application.state.services = services
        if settings.app_process_role == "all" and settings.openf1_live_auto_connect:
            # Combined mode ingests as well as serves, so it must take the same
            # singleton lease the dedicated ingestor uses. Without it, two
            # overlapping deploys would both subscribe to OpenF1 MQTT and
            # double-write the event pipeline.
            if not await services.database.acquire_ingestor_lease():
                raise RuntimeError("Another Apex Arena ingestor owns the singleton lease")
            await services.start_live_services()
        try:
            yield
        finally:
            await services.close()

    application = FastAPI(
        title="Apex Arena API",
        version="1.0.0",
        description="Unified live and replay Formula racing intelligence for the 2026 season.",
        lifespan=lifespan,
    )
    # Registered before CORS so the outermost layer rejects direct-origin traffic
    # before any other handler observes the request.
    application.add_middleware(ProxyContextMiddleware, settings=settings)
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
