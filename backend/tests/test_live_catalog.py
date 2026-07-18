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
    services.settings = SimpleNamespace(openf1_live_catalog_sync_seconds=60)
    services.openf1_live = SimpleNamespace(connect=AsyncMock())
    services.rooms = SimpleNamespace(force_sync=AsyncMock(return_value=1))
    services._live_catalog_task = None

    await services.start_live_services()
    await asyncio.sleep(0)

    services.openf1_live.connect.assert_awaited_once()
    services.rooms.force_sync.assert_awaited_once()
    assert services._live_catalog_task is not None
    services._live_catalog_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await services._live_catalog_task


@pytest.mark.asyncio
async def test_live_catalog_keeps_running_after_provider_failure() -> None:
    services = object.__new__(AppServices)
    services.settings = SimpleNamespace(openf1_live_catalog_sync_seconds=0)
    services.rooms = SimpleNamespace(
        force_sync=AsyncMock(side_effect=[RuntimeError("provider unavailable"), 1])
    )

    task = asyncio.create_task(services._maintain_live_catalog())
    for _ in range(10):
        await asyncio.sleep(0)
        if services.rooms.force_sync.await_count >= 2:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert services.rooms.force_sync.await_count >= 2
