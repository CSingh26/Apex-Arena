# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI

from app.core.logging import configure_logging
from app.core.settings import Settings, get_settings
from app.services.container import AppServices


def create_ingestor_app(settings_override: Settings | None = None) -> FastAPI:
    settings = settings_override or get_settings()
    if settings.app_process_role not in {"ingestor", "all"}:
        raise RuntimeError("The ingestor command requires APP_PROCESS_ROLE=ingestor or all")

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings)
        services = AppServices(settings)
        application.state.services = services
        try:
            if not await services.database.acquire_ingestor_lease():
                raise RuntimeError("Another Apex Arena ingestor owns the singleton lease")
            if settings.openf1_live_auto_connect:
                await services.start_live_services()
            yield
        finally:
            await services.close()

    application = FastAPI(
        title="Apex Arena Ingestor",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @application.get("/health/live")
    async def liveness() -> dict[str, object]:
        return {
            "status": "alive",
            "role": settings.app_process_role,
            "checked_at": datetime.now(UTC).isoformat(),
        }

    @application.get("/health/provider")
    async def provider_status() -> dict[str, object]:
        services: AppServices = application.state.services
        live = services.openf1_live.status()
        return {
            "status": str(live["connection_state"]).lower(),
            "role": settings.app_process_role,
            "current_session_key": live["current_session_key"],
            "last_event_at": live["last_event_at"],
            "reconnect_attempts": live["reconnect_attempts"],
        }

    return application
