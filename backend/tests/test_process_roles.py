# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.ingestor import create_ingestor_app
from app.main import create_app


def settings_with(settings: Settings, **changes: object) -> Settings:
    return Settings.model_validate({**settings.model_dump(), **changes})


def test_api_role_never_starts_live_ingestion(settings: Settings) -> None:
    api_settings = settings_with(
        settings,
        app_env="staging",
        app_process_role="api",
        openf1_live_auto_connect=True,
    )
    with patch("app.main.AppServices.start_live_services", new_callable=AsyncMock) as start:
        with TestClient(create_app(api_settings)) as client:
            assert client.get("/health/live").status_code == 200

    start.assert_not_awaited()


def test_ingestor_role_owns_live_startup_and_health(settings: Settings) -> None:
    ingestor_settings = settings_with(
        settings,
        app_process_role="ingestor",
        openf1_live_auto_connect=True,
    )
    with patch("app.ingestor.AppServices.start_live_services", new_callable=AsyncMock) as start:
        with TestClient(create_ingestor_app(ingestor_settings)) as client:
            response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json()["role"] == "ingestor"
    start.assert_awaited_once()
