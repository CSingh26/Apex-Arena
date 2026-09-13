# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.domain.locations import (
    DriverLocationSample,
    SessionTrackGeometry,
    TrackBounds,
    bounds_from_points,
    is_transmitting,
    is_valid_coordinate,
    percentile_bounds,
    simplify_path,
)
from app.services.locations import (
    LiveLocationRecorder,
    LocationIngestionService,
    LocationUnavailableError,
    SessionLocationService,
    build_track_geometry,
    close_single_lap,
    downsample,
    find_lap_window,
    parse_location_rows,
)
from app.services.race_state import DriverRaceState, RaceState
from app.services.session_realtime import location_state, location_state_from_samples

START = datetime(2026, 7, 19, 13, 0, tzinfo=UTC)


def provider_row(
    driver: int,
    seconds: float,
    x: float,
    y: float,
    z: float | None = 100,
    **overrides: Any,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "date": (START + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z"),
        "driver_number": driver,
        "session_key": 11334,
        "meeting_key": 1290,
        "x": x,
        "y": y,
        "z": z,
    }
    row.update(overrides)
    return row


def sample(driver: int, seconds: float, x: float, y: float) -> DriverLocationSample:
    return DriverLocationSample(
        driver_number=driver,
        x=x,
        y=y,
        z=10.0,
        sample_time=START + timedelta(seconds=seconds),
    )


class FakeLocationRepository:
    """In-memory stand-in with the same uniqueness rule as PostgreSQL."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, int, datetime], DriverLocationSample] = {}
        self.geometry: dict[str, SessionTrackGeometry] = {}

    async def bulk_insert(
        self,
        session_key: str,
        samples: list[DriverLocationSample],
        *,
        source: str = "historical",
        chunk_size: int = 1000,
    ) -> int:
        inserted = 0
        for item in samples:
            key = (session_key, item.driver_number, item.sample_time)
            if key in self.rows:
                continue
            self.rows[key] = item
            inserted += 1
        return inserted

    def _for(self, session_key: str) -> list[DriverLocationSample]:
        return sorted(
            (value for (key, _, _), value in self.rows.items() if key == session_key),
            key=lambda item: (item.sample_time, item.driver_number),
        )

    async def count(self, session_key: str) -> int:
        return len(self._for(session_key))

    async def time_range(self, session_key: str) -> tuple[datetime | None, datetime | None]:
        rows = self._for(session_key)
        return (rows[0].sample_time, rows[-1].sample_time) if rows else (None, None)

    async def driver_numbers(self, session_key: str) -> list[int]:
        return sorted({row.driver_number for row in self._for(session_key)})

    async def latest_per_driver(
        self, session_key: str, *, at: datetime | None = None
    ) -> list[DriverLocationSample]:
        latest: dict[int, DriverLocationSample] = {}
        for row in self._for(session_key):
            if at is not None and row.sample_time > at:
                continue
            current = latest.get(row.driver_number)
            if current is None or row.sample_time >= current.sample_time:
                latest[row.driver_number] = row
        return [latest[key] for key in sorted(latest)]

    async def window(
        self,
        session_key: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        driver_number: int | None = None,
        limit: int = 20_000,
    ) -> list[DriverLocationSample]:
        rows = [
            row
            for row in self._for(session_key)
            if (since is None or row.sample_time >= since)
            and (until is None or row.sample_time <= until)
            and (driver_number is None or row.driver_number == driver_number)
        ]
        return rows[:limit]

    async def sample_points(
        self, session_key: str, limit: int = 40_000
    ) -> list[tuple[float, float]]:
        return [(row.x, row.y) for row in self._for(session_key)][:limit]

    async def save_geometry(self, geometry: SessionTrackGeometry) -> None:
        self.geometry[geometry.session_key] = geometry

    async def get_geometry(self, session_key: str) -> SessionTrackGeometry | None:
        return self.geometry.get(session_key)


class FakeProvider:
    def __init__(
        self,
        rows: list[dict[str, Any]],
        *,
        session_window: tuple[str, str] = (
            "2026-07-19T13:00:00+00:00",
            "2026-07-19T13:04:00+00:00",
        ),
        fail_windows: int = 0,
    ) -> None:
        self.rows = rows
        self.session_window = session_window
        self.fail_windows = fail_windows
        self.calls: list[dict[str, Any]] = []

    async def sessions(self, **filters: Any) -> list[dict[str, Any]]:
        return [{"date_start": self.session_window[0], "date_end": self.session_window[1]}]

    async def location(self, **filters: Any) -> list[dict[str, Any]]:
        self.calls.append(filters)
        if self.fail_windows > 0:
            self.fail_windows -= 1
            raise RuntimeError("provider unavailable")
        start = datetime.fromisoformat(filters["date>"]).replace(tzinfo=UTC)
        end = datetime.fromisoformat(filters["date<"]).replace(tzinfo=UTC)
        driver = filters.get("driver_number")
        selected = []
        for row in self.rows:
            when = datetime.fromisoformat(row["date"].replace("Z", "+00:00"))
            if not (start < when < end):
                continue
            if driver is not None and row["driver_number"] != driver:
                continue
            selected.append(row)
        return selected


# --- provider parsing -------------------------------------------------------


def test_parse_reads_the_documented_provider_fields() -> None:
    samples, rejected = parse_location_rows([provider_row(4, 0, 218, -3473, -110)])
    assert rejected == 0
    assert samples[0].driver_number == 4
    assert (samples[0].x, samples[0].y, samples[0].z) == (218.0, -3473.0, -110.0)
    assert samples[0].sample_time == START


def test_parse_keeps_multiple_drivers_and_timestamps() -> None:
    samples, _ = parse_location_rows(
        [provider_row(1, 0, 10, 20), provider_row(4, 0, 30, 40), provider_row(1, 1, 11, 21)]
    )
    assert len(samples) == 3
    assert {item.driver_number for item in samples} == {1, 4}


def test_parse_accepts_a_missing_z() -> None:
    samples, rejected = parse_location_rows([{**provider_row(4, 0, 5, 6), "z": None}])
    assert rejected == 0
    assert samples[0].z is None


def test_parse_rejects_null_and_malformed_coordinates() -> None:
    samples, rejected = parse_location_rows(
        [
            provider_row(4, 0, None, 5),  # type: ignore[arg-type]
            provider_row(4, 1, "not-a-number", 5),  # type: ignore[arg-type]
            {"driver_number": 4, "x": 1, "y": 2},  # no date
            provider_row(None, 2, 1, 2),  # type: ignore[arg-type]
        ]
    )
    assert samples == []
    assert rejected == 4


def test_parse_rejects_the_provider_not_transmitting_marker() -> None:
    samples, rejected = parse_location_rows([provider_row(4, 0, 0, 0, 0)])
    assert samples == []
    assert rejected == 1
    assert is_transmitting(0.0, 0.0, 0.0) is False
    assert is_transmitting(0.0, 0.0, 12.0) is True


def test_parse_handles_an_empty_provider_response() -> None:
    assert parse_location_rows([]) == ([], 0)


def test_coordinate_guard_rejects_non_finite_and_absurd_values() -> None:
    assert is_valid_coordinate(float("nan")) is False
    assert is_valid_coordinate(float("inf")) is False
    assert is_valid_coordinate(10_000_000) is False
    assert is_valid_coordinate(True) is False
    assert is_valid_coordinate(-4330) is True


def test_string_coordinates_keep_full_precision() -> None:
    samples, _ = parse_location_rows([provider_row(4, 0, "1234.75", "-987.25")])  # type: ignore[arg-type]
    assert (samples[0].x, samples[0].y) == (1234.75, -987.25)


# --- downsampling -----------------------------------------------------------


def test_downsample_thins_each_driver_independently() -> None:
    samples = [sample(1, index * 0.25, index, 0) for index in range(9)]
    samples += [sample(4, index * 0.25, index, 1) for index in range(9)]
    reduced = downsample(samples, 1000)
    per_driver = {1: 0, 4: 0}
    for item in reduced:
        per_driver[item.driver_number] += 1
    assert per_driver == {1: 3, 4: 3}


def test_downsample_disabled_keeps_every_sample() -> None:
    samples = [sample(1, index * 0.25, index, 0) for index in range(5)]
    assert len(downsample(samples, 0)) == 5


# --- geometry ---------------------------------------------------------------


def circle_points(count: int, radius: float = 4000.0) -> list[tuple[float, float]]:
    import math

    return [
        (
            radius * math.cos(2 * math.pi * index / count),
            radius * math.sin(2 * math.pi * index / count),
        )
        for index in range(count)
    ]


def test_close_single_lap_cuts_a_repeated_trace_to_one_loop() -> None:
    loop = circle_points(60)
    lap = close_single_lap(loop + loop)
    assert 55 <= len(lap) <= 65


def test_close_single_lap_keeps_a_trace_that_never_returns() -> None:
    line = [(float(index) * 500, 0.0) for index in range(40)]
    assert close_single_lap(line) == line


def test_find_lap_window_ignores_a_stationary_car() -> None:
    parked = [sample(4, index, 10, 10) for index in range(200)]
    assert find_lap_window(parked) is None


def test_find_lap_window_locates_a_lap_after_garage_time() -> None:
    garage = [sample(4, index, 10, 10) for index in range(120)]
    loop = [
        sample(4, 120 + index, point[0], point[1])
        for index, point in enumerate(circle_points(90) * 2)
    ]
    window = find_lap_window(garage + loop)
    assert window is not None
    assert window[0] >= START + timedelta(seconds=100)


def test_build_geometry_preserves_shape_and_survives_outliers() -> None:
    loop = circle_points(120)
    # One recovery-truck fix far off the circuit must not decide the viewport.
    session_points = loop * 3 + [(500_00.0, -400_00.0)]
    geometry = build_track_geometry("11334", loop, session_points)
    assert geometry is not None
    assert geometry.bounds.max_x < 10_000
    assert geometry.bounds.min_x > -10_000
    assert abs(geometry.bounds.width - geometry.bounds.height) < 500
    assert len(geometry.path) >= 20


def test_build_geometry_closes_the_loop() -> None:
    loop = circle_points(120)
    geometry = build_track_geometry("11334", loop, loop)
    assert geometry is not None
    assert geometry.path[0] == geometry.path[-1]


def test_build_geometry_returns_none_without_usable_points() -> None:
    assert build_track_geometry("11334", [], []) is None
    assert build_track_geometry("11334", [(5.0, 5.0)], [(5.0, 5.0)]) is None


def test_percentile_bounds_trim_extremes_but_bounds_from_points_does_not() -> None:
    points = [(float(index), float(index)) for index in range(100)] + [(1e6, 1e6)]
    trimmed = percentile_bounds(points)
    raw = bounds_from_points(points)
    assert trimmed is not None and raw is not None
    assert trimmed.max_x < 1000
    assert raw.max_x == 1e6


def test_simplify_path_respects_the_point_ceiling() -> None:
    dense = circle_points(4000, radius=9000)
    assert len(simplify_path(dense, 5.0, max_points=200)) <= 201


def test_track_bounds_union() -> None:
    left = TrackBounds(min_x=0, max_x=10, min_y=0, max_y=10)
    right = TrackBounds(min_x=-5, max_x=5, min_y=2, max_y=20)
    merged = left.union(right)
    assert (merged.min_x, merged.max_x, merged.min_y, merged.max_y) == (-5, 10, 0, 20)
    assert left.union(None) is left


# --- ingestion --------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingestion_walks_the_session_window_and_stores_a_series() -> None:
    rows = [
        provider_row(driver, second + offset / 4, 100 * second, 50 * second)
        for driver in (1, 4)
        for second in range(240)
        for offset in range(4)
    ]
    repository = FakeLocationRepository()
    service = LocationIngestionService(
        client=FakeProvider(rows),
        repository=repository,
        sample_interval_ms=1000,
        fetch_window_seconds=60,
    )
    summary = await service.ingest_session("11334")
    assert summary.windows_requested == 4
    assert summary.windows_failed == 0
    assert summary.drivers == [1, 4]
    assert summary.total_samples > 400
    assert summary.first_sample_at is not None and summary.last_sample_at is not None
    assert summary.last_sample_at > summary.first_sample_at


@pytest.mark.asyncio
async def test_ingestion_is_idempotent() -> None:
    rows = [provider_row(1, second, second * 10, second * 5) for second in range(120)]
    repository = FakeLocationRepository()
    service = LocationIngestionService(
        client=FakeProvider(rows), repository=repository, fetch_window_seconds=60
    )
    first = await service.ingest_session("11334")
    second = await service.ingest_session("11334")
    assert second.stored_samples == 0
    assert second.total_samples == first.total_samples


@pytest.mark.asyncio
async def test_ingestion_retries_failed_windows() -> None:
    rows = [provider_row(1, second, second * 10, second * 5) for second in range(240)]
    provider = FakeProvider(rows, fail_windows=2)
    service = LocationIngestionService(
        client=provider,
        repository=FakeLocationRepository(),
        fetch_window_seconds=60,
        window_retry_passes=2,
    )
    summary = await service.ingest_session("11334")
    assert summary.windows_failed == 0
    assert summary.total_samples > 0


@pytest.mark.asyncio
async def test_ingestion_reports_when_the_provider_has_nothing() -> None:
    service = LocationIngestionService(
        client=FakeProvider([]), repository=FakeLocationRepository(), fetch_window_seconds=60
    )
    with pytest.raises(LocationUnavailableError):
        await service.ingest_session("11334")


@pytest.mark.asyncio
async def test_ingestion_rejects_a_session_without_a_time_window() -> None:
    provider = FakeProvider([])
    provider.session_window = ("", "")
    service = LocationIngestionService(client=provider, repository=FakeLocationRepository())
    with pytest.raises(LocationUnavailableError):
        await service.ingest_session("11334")


# --- query views ------------------------------------------------------------


@pytest.mark.asyncio
async def test_latest_per_driver_uses_at_or_before_not_exact_match() -> None:
    repository = FakeLocationRepository()
    await repository.bulk_insert(
        "11334",
        [sample(1, 0, 10, 10), sample(1, 5, 20, 20), sample(4, 3, 30, 30), sample(4, 9, 40, 40)],
    )
    service = SessionLocationService(repository)
    # Deliberately a timestamp no driver sampled on.
    latest = await service.latest("11334", at=START + timedelta(seconds=6))
    positions = {item.driver_number: (item.x, item.y) for item in latest}
    assert positions == {1: (20.0, 20.0), 4: (30.0, 30.0)}


@pytest.mark.asyncio
async def test_window_filters_by_driver_and_time() -> None:
    repository = FakeLocationRepository()
    await repository.bulk_insert(
        "11334", [sample(1, second, second, second) for second in range(10)]
    )
    await repository.bulk_insert(
        "11334", [sample(4, second, second, second) for second in range(10)]
    )
    service = SessionLocationService(repository)
    rows = await service.window(
        "11334",
        since=START + timedelta(seconds=2),
        until=START + timedelta(seconds=4),
        driver_number=4,
    )
    assert [item.driver_number for item in rows] == [4, 4, 4]


@pytest.mark.asyncio
async def test_sessions_do_not_share_samples() -> None:
    repository = FakeLocationRepository()
    await repository.bulk_insert("11334", [sample(1, 0, 1, 1)])
    await repository.bulk_insert("11330", [sample(4, 0, 2, 2)])
    service = SessionLocationService(repository)
    assert await service.sample_count("11334") == 1
    assert [item.driver_number for item in await service.latest("11330")] == [4]


# --- live recorder ----------------------------------------------------------


class StubEvent:
    def __init__(self, event_type: str, payload: dict[str, Any]) -> None:
        self.event_type = type("Kind", (), {"value": event_type})()
        self.payload = payload
        self.session_key = "11334"


@pytest.mark.asyncio
async def test_live_recorder_persists_location_samples_only() -> None:
    repository = FakeLocationRepository()
    recorder = LiveLocationRecorder(SessionLocationService(repository))
    await recorder.consume(StubEvent("LOCATION_SAMPLE", provider_row(16, 0, 100, 200)))
    await recorder.consume(StubEvent("CAR_DATA_SAMPLE", {"speed": 300}))
    assert await repository.count("11334") == 1


@pytest.mark.asyncio
async def test_live_recorder_never_raises_into_the_pipeline() -> None:
    class Broken(SessionLocationService):
        async def record_live_samples(self, *args: Any, **kwargs: Any) -> int:
            raise RuntimeError("store offline")

    recorder = LiveLocationRecorder(Broken(FakeLocationRepository()))
    await recorder.consume(StubEvent("LOCATION_SAMPLE", provider_row(16, 0, 100, 200)))


# --- API view models --------------------------------------------------------


def race_state_with(drivers: dict[str, DriverRaceState]) -> RaceState:
    return RaceState(session_key="11334", sequence_number=42, drivers=drivers)


def test_location_state_from_samples_joins_timing_metadata() -> None:
    state = race_state_with(
        {"16": DriverRaceState(driver_number=16, broadcast_name="C LECLERC", position=2)}
    )
    view = location_state_from_samples(state, [sample(16, 0, 100, 200), sample(81, 0, 5, 6)])
    assert view.available is True
    assert view.source == "historical"
    by_number = {row.driver_number: row for row in view.drivers}
    assert by_number[16].abbreviation == "LEC"
    assert by_number[16].position == 2
    # An unknown driver still gets a marker, labelled by car number.
    assert by_number[81].abbreviation == "81"
    assert by_number[81].position is None


def test_location_state_drops_a_non_transmitting_car() -> None:
    state = race_state_with(
        {
            "1": DriverRaceState(driver_number=1, location={"x": 0, "y": 0, "z": 0}),
            "4": DriverRaceState(driver_number=4, location={"x": 10, "y": 20, "z": 30}),
        }
    )
    view = location_state(state)
    assert [row.driver_number for row in view.drivers] == [4]
    assert view.source == "live"


def test_location_state_is_unavailable_without_fixes() -> None:
    view = location_state(race_state_with({}))
    assert view.available is False
    assert view.source == "unavailable"
    assert view.drivers == []
