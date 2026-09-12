# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.container import AppServices


@pytest.mark.asyncio
async def test_live_services_connect_and_reconcile_catalog() -> None:
    services = object.__new__(AppServices)
    services.settings = SimpleNamespace(openf1_live_poll_seconds=60)
    services.openf1_live = SimpleNamespace(connect=AsyncMock())
    services.live_ingestion = SimpleNamespace(run_once=AsyncMock(return_value=None))
    services._live_catalog_task = None

    await services.start_live_services()
    await asyncio.sleep(0)

    services.openf1_live.connect.assert_awaited_once()
    services.live_ingestion.run_once.assert_awaited_once()
    assert services._live_catalog_task is not None
    services._live_catalog_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await services._live_catalog_task


@pytest.mark.asyncio
async def test_live_catalog_keeps_running_after_provider_failure() -> None:
    services = object.__new__(AppServices)
    services.settings = SimpleNamespace(openf1_live_poll_seconds=0)
    services.live_ingestion = SimpleNamespace(
        run_once=AsyncMock(side_effect=[RuntimeError("provider unavailable"), 1])
    )

    task = asyncio.create_task(services._maintain_live_catalog())
    for _ in range(10):
        await asyncio.sleep(0)
        if services.live_ingestion.run_once.await_count >= 2:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert services.live_ingestion.run_once.await_count >= 2


@pytest.mark.asyncio
async def test_api_does_not_report_a_dead_worker_as_live():
    services = object.__new__(AppServices)
    services.settings = SimpleNamespace(app_process_role="api")
    services.openf1_live = SimpleNamespace(status=lambda: {})
    services.event_bus = SimpleNamespace(
        latest_connection_status=AsyncMock(
            return_value={
                "connection_state": "LIVE",
                "checked_at": "2000-01-01T00:00:00+00:00",
                "ingestion_running": True,
                "provider_connected": True,
            }
        )
    )
    status = await services.provider_status()
    assert status["connection_state"] == "STALE"
    assert status["ingestion_running"] is False
