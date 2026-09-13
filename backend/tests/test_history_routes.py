# SPDX-License-Identifier: AGPL-3.0-only
import importlib.util
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from tests.test_history_details import reader
from tests.test_ingestion_recovery import processor, raw
from tests.test_intelligence_commit import intelligence_sql as intelligence_sql


@pytest.mark.asyncio
async def test_actual_detail_http_contract_has_bounded_selection_and_honest_failures(
    intelligence_sql, monkeypatch
):
    assert importlib.util.find_spec("app.api.history_routes") is not None, "detail routes missing"
    from app.api.history_routes import get_services, router
    from app.services.history_details import DetailBusyError, DetailViewChangedError

    pipeline, projection, public, _ = processor(intelligence_sql)
    await pipeline.ingest(raw(1, "laps", lap_number=1, lap_duration=90))
    service = reader(intelligence_sql)
    services = SimpleNamespace(
        history_details=service, race_state=public, intelligence_progress=projection.repository
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_services] = lambda: services
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        url = "/api/v1/sessions/race/intelligence-detail?driver=4&family=laps"
        response = await client.get(url)
        assert response.status_code == 200
        assert response.json()["view_sequence"] == 1
        assert (await client.get(url + "&driver=5&driver=6")).status_code == 422
        assert (await client.get(url.replace("/race/", "/absent/"))).status_code == 404

        async def busy(*args, **kwargs):
            raise DetailBusyError("busy")

        monkeypatch.setattr(service, "read_session", busy)
        response = await client.get(url)
        assert response.status_code == 503
        assert response.headers["retry-after"] == "1"

        async def changed(*args, **kwargs):
            raise DetailViewChangedError("view changed")

        monkeypatch.setattr(service, "read_session", changed)
        assert (await client.get(url)).status_code == 409
