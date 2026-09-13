# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import importlib
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize(
    "environment,enabled", [("production", True), ("staging", True), ("test", False)]
)
def test_fixture_provider_refuses_before_app_construction(environment, enabled, monkeypatch):
    module = importlib.import_module("app.cli.e2e_provider")

    def forbidden():
        raise AssertionError("must guard before constructing/binding the fixture app")

    monkeypatch.setattr(module, "FastAPI", forbidden)
    with pytest.raises(RuntimeError, match="local/test"):
        module.create_fixture_app(environment=environment, enabled=enabled)


async def test_fixture_provider_feeds_real_calendar_normalizer_and_only_allowlisted_routes(
    settings,
):
    from app.providers.jolpica import JolpicaClient
    from app.services.season import SeasonService

    module = importlib.import_module("app.cli.e2e_provider")
    now = datetime(2026, 9, 12, tzinfo=UTC)
    app = module.create_fixture_app(environment="test", enabled=True, now=now)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture/jolpica/"
    ) as client:
        season = SeasonService(settings, JolpicaClient("http://fixture/jolpica", client=client))
        meetings = await season.calendar(2026, now=now)
        assert [meeting.status.value for meeting in meetings] == [
            "completed",
            "completed",
            "upcoming",
        ]
        assert len(meetings[0].sessions) == len(meetings[1].sessions) == 5
        assert any(session.name == "Sprint" for session in meetings[1].sessions)
        assert meetings[2].sessions[0].starts_at > now
        assert all("Synthetic CI" in meeting.race_name for meeting in meetings)
    with TestClient(app) as client:
        assert (
            client.get("/openf1/weather", params={"session_key": "e2e-normal-race-v1"}).json() == []
        )
        assert client.get("/openf1/sessions", params={"year": "2026"}).status_code == 200
        assert client.get("/openf1/unknown").status_code == 404
        assert client.get("/jolpica/2025.json").status_code == 404
        for resource in ("championship_drivers", "championship_teams", "drivers"):
            assert (
                client.get(f"/openf1/{resource}", params={"session_key": "other"}).status_code
                == 404
            )


def test_fixture_uses_a_supported_circuit_dossier():
    from app.cli.e2e_fixtures import fixture_room
    from app.services.circuit_intelligence import CircuitIntelligenceService

    assert (
        len(
            CircuitIntelligenceService()
            .for_circuit(fixture_room("e2e-normal-race").circuit_name)
            .records
        )
        == 3
    )


async def test_fixture_supports_navigation_championship_prefetch(settings):
    from app.cli.e2e_provider import create_fixture_app
    from app.providers.jolpica import JolpicaClient
    from app.providers.openf1 import OpenF1RestClient
    from app.services.championship import ChampionshipService
    from app.services.season import SeasonService
    from tests.test_championship import MemoryRedis

    app = create_fixture_app(environment="test", enabled=True)
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://fixture/jolpica/") as jolpica_http,
        httpx.AsyncClient(transport=transport, base_url="http://fixture/openf1/") as openf1_http,
    ):
        jolpica = JolpicaClient("http://fixture/jolpica", client=jolpica_http)
        openf1 = OpenF1RestClient(settings, client=openf1_http)
        championship = ChampionshipService(
            season=2026,
            openf1=openf1,
            jolpica=jolpica,
            season_service=SeasonService(settings, jolpica),
            redis=MemoryRedis(),
        )
        drivers = await championship.drivers()
        constructors = await championship.constructors()
        assert len(drivers.standings) == 3
        assert all("Synthetic" in driver.full_name for driver in drivers.standings)
        assert constructors.standings[0].team_name == "Synthetic CI Team"
