# SPDX-License-Identifier: AGPL-3.0-only
"""HTTP contract for bounded telemetry reads."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.api.history_routes import get_services, router
from app.services.telemetry_history import TelemetryHistoryService
from tests.test_telemetry_history import FakeEvents, car_data


def client_for(events, *, sequence: int = 10_000):
    async def state(_key):
        return SimpleNamespace(sequence_number=sequence)

    services = SimpleNamespace(
        telemetry_history=TelemetryHistoryService(
            FakeEvents(events), SimpleNamespace(get_state=state)
        )
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_services] = lambda: services
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_telemetry_contract_is_bounded_and_explains_missing_data():
    events = [car_data(1, 4), car_data(2, 4), car_data(3, 16)]
    async with client_for(events) as client:
        response = await client.get("/api/v1/sessions/race/telemetry-history?driver=4&driver=16")
        assert response.status_code == 200
        body = response.json()
        assert body["availability"] == "available"
        assert body["units"]["speed"] == "km/h"
        assert {row["driver_number"] for row in body["drivers"]} == {4, 16}

        # More than two drivers is outside the comparison contract.
        too_many = await client.get(
            "/api/v1/sessions/race/telemetry-history?driver=4&driver=16&driver=81"
        )
        assert too_many.status_code == 422

        missing = await client.get("/api/v1/sessions/absent/telemetry-history?driver=4")
        assert missing.status_code == 200
        assert missing.json()["availability"] == "unavailable"
        assert missing.json()["reason"] == "no_telemetry_retained"


@pytest.mark.asyncio
async def test_a_storage_failure_is_a_retryable_503_not_a_fabricated_trace(monkeypatch):
    async with client_for([car_data(1, 4)]) as client:
        response = await client.get("/api/v1/sessions/race/telemetry-history?driver=4")
        assert response.status_code == 200

        services = client._transport.app.dependency_overrides[get_services]()

        async def broken(*args, **kwargs):
            raise RuntimeError("synthetic storage failure")

        monkeypatch.setattr(services.telemetry_history, "read", broken)
        failed = await client.get("/api/v1/sessions/race/telemetry-history?driver=4")
        assert failed.status_code == 503
        assert failed.headers["retry-after"] == "1"


@pytest.mark.asyncio
async def test_negative_or_malformed_selection_is_rejected_by_validation():
    async with client_for([]) as client:
        assert (await client.get("/api/v1/sessions/race/telemetry-history")).status_code == 422
        assert (
            await client.get("/api/v1/sessions/race/telemetry-history?driver=4&lap=-1")
        ).status_code == 422
        malformed = await client.get("/api/v1/sessions/race/telemetry-history?driver=nine")
        assert malformed.status_code == 422
