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
from app.services.race_state import DriverRaceState, RaceState


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


def test_health_probes_separate_liveness_readiness_and_provider(settings: Settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        services = app.state.services
        services.database.health_check = AsyncMock(return_value=(True, "connected"))
        services.redis.health_check = AsyncMock(return_value=(False, "unavailable"))
        services.event_bus.latest_connection_status = AsyncMock(
            return_value={
                "connection_state": "CONNECTED",
                "current_session_key": "spa-race",
                "last_event_at": "2026-07-19T12:00:00Z",
            }
        )

        live_response = client.get("/health/live")
        ready_response = client.get("/health/ready")
        provider_response = client.get("/health/provider")

    assert live_response.status_code == 200
    assert live_response.json()["role"] == "api"
    assert ready_response.status_code == 503
    assert ready_response.json()["dependencies"]["redis"] == "unavailable"
    assert provider_response.status_code == 200
    assert provider_response.json()["connection_state"] == "CONNECTED"
    assert "credentials" not in provider_response.text.lower()


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
            return_value=RaceState(
                session_key="spa-race",
                session_type="RACE",
                status="running",
                sequence_number=4,
                is_replay=True,
                drivers={
                    "4": DriverRaceState(
                        driver_number=4,
                        full_name="Lando Norris",
                        position=1,
                        telemetry={"speed": 301.2},
                        location={"x": 10.0, "y": 20.0, "z": 1.0},
                    )
                },
            )
        )

        events_response = client.get("/api/v1/sessions/spa-race/events?after_sequence_number=3")
        state_response = client.get("/api/v1/sessions/spa-race/state")
        timing_response = client.get("/api/v1/sessions/spa-race/timing")
        telemetry_response = client.get("/api/v1/sessions/spa-race/drivers/4/telemetry")
        locations_response = client.get("/api/v1/sessions/spa-race/locations")

    assert events_response.json()["events"][0]["sequence_number"] == 4
    assert state_response.json()["state"]["is_replay"] is True
    assert timing_response.json()["timing"]["drivers"][0]["abbreviation"] == "NOR"
    assert telemetry_response.json()["telemetry"]["speed_kph"] == 301.2
    assert locations_response.json()["locations"]["drivers"][0]["x"] == 10.0


def test_championship_endpoints_expose_normalized_responses(settings: Settings) -> None:
    app = create_app(settings)
    metadata = {
        "season": 2026,
        "generated_at": "2026-07-19T16:00:00Z",
        "latest_completed_event": "Belgian Grand Prix",
        "races_completed": 13,
        "races_remaining": 11,
        "source": "OpenF1 + Jolpica",
        "live": False,
    }
    with TestClient(app) as client:
        services = app.state.services
        services.championship.drivers = AsyncMock(
            return_value={"standings": [], "metadata": metadata}
        )
        services.championship.constructors = AsyncMock(
            return_value={"standings": [], "metadata": metadata}
        )
        services.championship.summary = AsyncMock(
            return_value={
                "driver_leader": None,
                "constructor_leader": None,
                "races_completed": 13,
                "races_remaining": 11,
                "latest_race": "Belgian Grand Prix",
                "next_race": "Hungarian Grand Prix",
                "metadata": metadata,
            }
        )

        drivers = client.get("/api/v1/championship/drivers")
        constructors = client.get("/api/v1/championship/constructors")
        summary = client.get("/api/v1/championship/summary")

    assert drivers.status_code == 200
    assert drivers.json()["metadata"]["source"] == "OpenF1 + Jolpica"
    assert constructors.status_code == 200
    assert summary.status_code == 200
    assert summary.json()["next_race"] == "Hungarian Grand Prix"


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


def test_debug_config_is_available_outside_production(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/debug/config")

    assert response.status_code == 200
    assert "features" in response.json()


def test_debug_config_is_hidden_in_production_without_flag(settings: Settings) -> None:
    hardened = Settings.model_validate(
        {
            **settings.model_dump(),
            "app_env": "production",
            "room_diagnostics_enabled": False,
            "debug_ingestion_enabled": False,
            "openf1_live_auto_connect": False,
            "database_url": (
                "postgresql://apex:test-password@localhost:5432/apex_arena?ssl=require"
            ),
            "redis_url": "rediss://localhost:6379/15",
        }
    )
    with TestClient(create_app(hardened)) as client:
        response = client.get("/api/v1/debug/config")

    # The endpoint must not leak internal database/redis hostnames publicly.
    assert response.status_code == 404
    assert "database_host" not in response.text


def _location_service(samples=None, geometry=None, error: Exception | None = None):
    from app.domain.locations import DriverLocationSample, SessionTrackGeometry, TrackBounds

    class FakeSessionLocations:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        async def latest(self, session_key: str, *, at=None):
            self.calls.append((session_key, at))
            if error is not None:
                raise error
            return [DriverLocationSample(**row) for row in (samples or [])]

        async def window(self, session_key: str, **kwargs):
            self.calls.append((session_key, kwargs))
            return [DriverLocationSample(**row) for row in (samples or [])]

        async def geometry(self, session_key: str):
            if geometry is None:
                return None
            return SessionTrackGeometry(
                session_key=session_key,
                bounds=TrackBounds(**geometry["bounds"]),
                path=geometry["path"],
                source_driver_number=geometry.get("driver"),
                sample_count=geometry.get("sample_count", 0),
            )

        async def time_range(self, session_key: str):
            return (None, None)

        async def sample_count(self, session_key: str) -> int:
            return len(samples or [])

    return FakeSessionLocations()


def _sample(driver: int, second: int, x: float, y: float) -> dict[str, object]:
    return {
        "driver_number": driver,
        "x": x,
        "y": y,
        "z": 5.0,
        "sample_time": datetime(2026, 7, 19, 13, 0, second, tzinfo=UTC),
    }


def test_location_snapshot_serves_a_reconnecting_browser_without_waiting(
    settings: Settings,
) -> None:
    """A fresh connection must get every known position immediately."""

    app = create_app(settings)
    with TestClient(app) as client:
        services = app.state.services
        services.race_state.get_state = AsyncMock(
            return_value=RaceState(session_key="11334", sequence_number=9)
        )
        services.session_locations = _location_service(
            samples=[_sample(1, 0, 100.0, -200.0), _sample(16, 1, 300.5, -450.25)],
            geometry={
                "bounds": {"min_x": -4330, "max_x": 8311, "min_y": -15762, "max_y": 4537},
                "path": [[0.0, 0.0], [10.0, 10.0]],
                "driver": 1,
            },
        )
        response = client.get("/api/v1/sessions/11334/locations")

    body = response.json()["locations"]
    assert response.status_code == 200
    assert body["available"] is True
    assert body["source"] == "historical"
    assert [row["driver_number"] for row in body["drivers"]] == [1, 16]
    assert body["drivers"][1]["x"] == 300.5
    assert body["bounds"]["min_x"] == -4330


def test_location_snapshot_accepts_a_replay_clock(settings: Settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        services = app.state.services
        services.race_state.get_state = AsyncMock(
            return_value=RaceState(session_key="11334", sequence_number=9)
        )
        fake = _location_service(samples=[_sample(1, 0, 1.0, 2.0)])
        services.session_locations = fake
        response = client.get("/api/v1/sessions/11334/locations?at=2026-07-19T13:10:00Z")

    assert response.status_code == 200
    assert fake.calls[0][0] == "11334"
    assert fake.calls[0][1] == datetime(2026, 7, 19, 13, 10, tzinfo=UTC)


def test_location_snapshot_prefers_live_state_over_history(settings: Settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        services = app.state.services
        services.race_state.get_state = AsyncMock(
            return_value=RaceState(
                session_key="11334",
                sequence_number=9,
                drivers={
                    "4": DriverRaceState(
                        driver_number=4, position=1, location={"x": 7.0, "y": 8.0, "z": 9.0}
                    )
                },
            )
        )
        services.session_locations = _location_service(samples=[_sample(1, 0, 1.0, 2.0)])
        response = client.get("/api/v1/sessions/11334/locations")

    body = response.json()["locations"]
    assert body["source"] == "live"
    assert [row["driver_number"] for row in body["drivers"]] == [4]


def test_location_endpoint_degrades_without_taking_the_room_down(settings: Settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        services = app.state.services
        services.race_state.get_state = AsyncMock(
            return_value=RaceState(session_key="11334", sequence_number=9)
        )
        services.session_locations = _location_service(error=RuntimeError("store offline"))
        locations = client.get("/api/v1/sessions/11334/locations")
        timing = client.get("/api/v1/sessions/11334/timing")

    assert locations.status_code == 503
    assert "offline" not in locations.text
    # The rest of the Race Room is unaffected by a location outage.
    assert timing.status_code == 200


def test_location_samples_window_is_scoped_to_one_session(settings: Settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        services = app.state.services
        fake = _location_service(samples=[_sample(1, 0, 1.0, 2.0), _sample(16, 1, 3.0, 4.0)])
        services.session_locations = fake
        response = client.get(
            "/api/v1/sessions/11334/locations/samples"
            "?since=2026-07-19T13:00:00Z&until=2026-07-19T13:00:30Z&limit=500"
        )
        other = client.get("/api/v1/sessions/11330/locations/samples")

    body = response.json()["locations"]
    assert body["session_key"] == "11334"
    assert body["count"] == 2
    assert body["drivers"] == [1, 16]
    assert fake.calls[0][1]["since"] == datetime(2026, 7, 19, 13, tzinfo=UTC)
    assert fake.calls[0][1]["limit"] == 500
    # A different session key never reads the first session's window.
    assert other.json()["locations"]["session_key"] == "11330"
    assert fake.calls[1][0] == "11330"


def test_track_endpoint_returns_geometry_in_provider_coordinates(settings: Settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        app.state.services.session_locations = _location_service(
            geometry={
                "bounds": {"min_x": -4330, "max_x": 8311, "min_y": -15762, "max_y": 4537},
                "path": [[-4330.0, -15762.0], [8311.0, 4537.0]],
                "driver": 1,
                "sample_count": 108178,
            }
        )
        response = client.get("/api/v1/sessions/11334/track")

    track = response.json()["track"]
    assert track["available"] is True
    assert track["bounds"]["max_y"] == 4537
    assert track["path"][0] == [-4330.0, -15762.0]
    assert track["sample_count"] == 108178


def test_track_endpoint_reports_a_session_without_geometry(settings: Settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        app.state.services.session_locations = _location_service()
        response = client.get("/api/v1/sessions/11334/track")

    track = response.json()["track"]
    assert track["available"] is False
    assert track["bounds"] is None
    assert track["path"] == []
