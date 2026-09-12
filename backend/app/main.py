# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.proxy import REPLAY_OPERATOR_HEADER, ProxyContextMiddleware, replay_operator_password
from app.api.rate_limits import RateLimitMiddleware
from app.api.room_routes import router as room_router
from app.api.routes import router
from app.core.logging import configure_logging
from app.core.settings import Settings, get_settings
from app.services.container import AppServices


def create_app(settings_override: Settings | None = None) -> FastAPI:
    settings = settings_override or get_settings()
    if (
        settings.app_env == "production"
        and settings.app_process_role in {"api", "combined", "all"}
        and settings.enable_public_replays
        and replay_operator_password(settings) is None
    ):
        raise RuntimeError(
            "ADMIN_DASHBOARD_PASSWORD is required when production public replay controls "
            "are enabled"
        )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings)
        services = AppServices(settings)
        application.state.services = services
        worker_enabled = settings.app_process_role in {"combined", "all"} and (
            settings.live_worker_enabled or settings.recent_session_reconciliation_enabled
        )
        try:
            if worker_enabled:
                # Combined instances share the dedicated ingestor's singleton lease.
                if not await services.database.acquire_ingestor_lease():
                    raise RuntimeError("Another Apex Arena ingestor owns the singleton lease")
            if settings.app_process_role in {"api", "combined", "all"}:
                await services.reconcile_interrupted_ingestion_runs()
                await services.start_replay_recovery()
            if settings.app_process_role in {"combined", "all"}:
                if settings.live_worker_enabled:
                    await services.start_live_services()
                await services.start_recent_reconciliation()
            yield
        finally:
            await services.close()

    application = FastAPI(
        title="Apex Arena API",
        version="1.0.0",
        description="Unified live and replay Formula racing intelligence for the 2026 season.",
        lifespan=lifespan,
    )
    # Execution order is CORS -> authenticated proxy hop -> Redis admission ->
    # routes. Rejections retain CORS; invalid proxy traffic never touches Redis.
    application.add_middleware(RateLimitMiddleware, settings=settings)
    application.add_middleware(ProxyContextMiddleware, settings=settings)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=[
            "Accept",
            "Content-Type",
            "X-Internal-API-Key",
            REPLAY_OPERATOR_HEADER,
        ],
    )
    application.include_router(router)
    application.include_router(room_router)
    return application


app = create_app()
