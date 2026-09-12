# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.main import create_app
from app.services.container import AppServices
from app.services.race_state import RaceState
from app.storage.redis import EventBus
from app.storage.room_repository import SqlRaceRoomRepository


@pytest.mark.parametrize("role", ["api", "combined", "all"])
async def test_actual_lifespan_recovers_at_start_and_defers_one_sweep(settings, monkeypatch, role):
    recovered = []

    async def recover(_repository):
        recovered.append("paused")
        return 1

    monkeypatch.setattr(SqlRaceRoomRepository, "pause_orphaned_running_rows", recover)
    original = AppServices.__init__

    def initialize(self, configured):
        original(self, configured)
        self.room_replay.lease_seconds = 0.03

    monkeypatch.setattr(AppServices, "__init__", initialize)
    application = create_app(
        settings.model_copy(
            update={
                "app_process_role": role,
                "openf1_live_auto_connect": False,
                "openf1_ingestion_mode": "disabled",
                "recent_session_reconciliation_enabled": False,
            }
        )
    )
    async with application.router.lifespan_context(application):
        assert recovered == ["paused"]
        await asyncio.sleep(0.08)
        assert recovered == ["paused", "paused"]
        await asyncio.sleep(0.05)
        assert len(recovered) == 2
    assert application.state.services._replay_reconciliation_task is None


async def test_lifespan_cleanup_cancels_deferred_sweep(settings, monkeypatch):
    recovered = []

    async def recover(_repository):
        recovered.append(True)
        return 1

    monkeypatch.setattr(SqlRaceRoomRepository, "pause_orphaned_running_rows", recover)
    application = create_app(settings.model_copy(update={"app_process_role": "api"}))
    async with application.router.lifespan_context(application):
        task = application.state.services._replay_reconciliation_task
    assert task.cancelled()
    assert recovered == [True]


async def test_startup_failure_always_closes_services(settings, monkeypatch):
    closed = []

    async def recover(_repository):
        raise RuntimeError("recovery unavailable")

    original_close = AppServices.close

    async def close(self):
        await original_close(self)
        closed.append(True)

    monkeypatch.setattr(SqlRaceRoomRepository, "pause_orphaned_running_rows", recover)
    monkeypatch.setattr(AppServices, "close", close)
    application = create_app(settings.model_copy(update={"app_process_role": "api"}))
    with pytest.raises(RuntimeError, match="recovery unavailable"):
        async with application.router.lifespan_context(application):
            raise AssertionError("startup should fail")
    assert closed == [True]


@pytest.mark.asyncio
async def test_api_services_refresh_race_state_monotonically_from_shared_bus(
    settings, monkeypatch
) -> None:
    shared = RaceState(session_key="live", sequence_number=8)

    async def latest_state(_bus: EventBus, session_key: str) -> RaceState:
        assert session_key == "live"
        return shared

    monkeypatch.setattr(EventBus, "latest_state", latest_state, raising=False)
    services = AppServices(settings.model_copy(update={"app_process_role": "api"}))
    services.snapshot_repository.latest = AsyncMock(return_value=None)  # type: ignore[method-assign]
    try:
        assert (await services.race_state.get_state("live")).sequence_number == 8
        shared = RaceState(session_key="live", sequence_number=3)
        assert (await services.race_state.get_state("live")).sequence_number == 8
    finally:
        await services.close()
