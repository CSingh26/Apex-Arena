# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.container import AppServices
from app.services.race_state import RaceState
from app.storage.redis import EventBus


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
