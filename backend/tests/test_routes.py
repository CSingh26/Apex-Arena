# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.domain.models import MeetingLifecycleStatus, RaceMeeting
from app.main import create_app


def test_health_reports_dependency_and_live_degradation(settings: Settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        app.state.services.database.health_check = AsyncMock(return_value=(True, "connected"))
        app.state.services.redis.health_check = AsyncMock(
            return_value=(False, "unavailable (ConnectionError)")
        )

        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["database"]["status"] == "healthy"
    assert body["redis"]["status"] == "degraded"
    assert body["openf1_live"]["status"] == "degraded"
    assert "password" not in response.text.lower()


def test_season_endpoint_returns_target_summary(settings: Settings) -> None:
    app = create_app(settings)
    spa = RaceMeeting(
        season_year=2026,
        round_number=13,
        race_name="Belgian Grand Prix",
        circuit_id="spa",
        circuit_name="Circuit de Spa-Francorchamps",
        locality="Spa",
        country="Belgium",
        race_date=date(2026, 7, 19),
        race_start=datetime(2026, 7, 19, 13, tzinfo=UTC),
        status=MeetingLifecycleStatus.UPCOMING,
        is_target=True,
    )

    with TestClient(app) as client:
        app.state.services.season.calendar = AsyncMock(return_value=[spa])
        response = client.get("/api/v1/season/2026")

    assert response.status_code == 200
    assert response.json()["target_found"] is True
    assert response.json()["races"][0]["circuit_id"] == "spa"


def test_other_seasons_are_rejected(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/season/2025")

    assert response.status_code == 404
