# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.domain.models import (
    MeetingLifecycleStatus,
    NormalizedRaceEvent,
    RaceEventType,
    RaceMeeting,
)
from app.main import create_app
from app.services.historical import HistoricalIngestionResult
from app.services.race_state import RaceState


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


def test_engine_status_reports_current_session_counts(settings: Settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        services = app.state.services
        services.database.health_check = AsyncMock(return_value=(True, "connected"))
        services.redis.health_check = AsyncMock(return_value=(True, "connected"))
        services.normalized_event_repository.latest_session_key = AsyncMock(return_value="spa-race")
        services.raw_event_repository.count = AsyncMock(return_value=12)
        services.normalized_event_repository.count = AsyncMock(return_value=10)
        services.snapshot_repository.count = AsyncMock(return_value=2)
        services.normalized_event_repository.max_sequence = AsyncMock(return_value=10)
        services.ingestion_runs.latest = AsyncMock(return_value=None)

        response = client.get("/api/v1/engine/status")

    assert response.status_code == 200
    assert response.json()["current_session_key"] == "spa-race"
    assert response.json()["latest_sequence_number"] == 10
    assert response.json()["redis"]["status"] == "healthy"


def test_session_events_and_state_are_exposed(settings: Settings) -> None:
    app = create_app(settings)
    event = NormalizedRaceEvent(
        session_key="spa-race",
        source="openf1_historical",
        event_time=datetime(2026, 7, 19, 13, tzinfo=UTC),
        received_at=datetime(2026, 7, 19, 13, tzinfo=UTC),
        sequence_number=4,
        event_type=RaceEventType.LAP_COMPLETED,
        dedup_key="dedup",
        is_replay=True,
    )
    with TestClient(app) as client:
        services = app.state.services
        services.normalized_event_repository.list_for_session = AsyncMock(return_value=[event])
        services.race_state.get_state = AsyncMock(
            return_value=RaceState(session_key="spa-race", sequence_number=4, is_replay=True)
        )

        events_response = client.get("/api/v1/sessions/spa-race/events?after_sequence_number=3")
        state_response = client.get("/api/v1/sessions/spa-race/state")

    assert events_response.json()["events"][0]["sequence_number"] == 4
    assert state_response.json()["state"]["is_replay"] is True


def test_historical_ingestion_requires_internal_key(settings: Settings) -> None:
    protected = Settings.model_validate(
        {**settings.model_dump(), "internal_api_key": "safe-internal-key"}
    )
    app = create_app(protected)
    with TestClient(app) as client:
        unauthorized = client.post(
            "/api/v1/debug/ingest-historical-session",
            json={"session_key": "9839", "endpoints": ["laps"]},
        )

    assert unauthorized.status_code == 401
    assert "safe-internal-key" not in unauthorized.text


def test_historical_ingestion_returns_pipeline_counts(settings: Settings) -> None:
    protected = Settings.model_validate(
        {**settings.model_dump(), "internal_api_key": "safe-internal-key"}
    )
    app = create_app(protected)
    ingestion_result = HistoricalIngestionResult(
        run_id="3b7f66d5-6786-4f83-bf65-2f540116a563",
        session_key="9839",
        endpoints=["laps"],
        fetched_records=3,
        raw_inserted=3,
        duplicates=0,
        normalized_inserted=3,
        normalized_duplicates=0,
        snapshots=1,
    )
    with TestClient(app) as client:
        app.state.services.historical.ingest_session = AsyncMock(return_value=ingestion_result)
        response = client.post(
            "/api/v1/debug/ingest-historical-session",
            headers={"X-Internal-API-Key": "safe-internal-key"},
            json={"session_key": "9839", "endpoints": ["laps"]},
        )

    assert response.status_code == 200
    assert response.json()["normalized_inserted"] == 3
