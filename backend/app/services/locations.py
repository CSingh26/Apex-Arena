# SPDX-License-Identifier: AGPL-3.0-only
"""Driver track position pipeline: provider fetch, storage, and query views.

Location is deliberately kept off the normalized replay event sequence. OpenF1
publishes roughly four fixes per car per second, so a single race is hundreds
of thousands of rows; pushing that through the event pipeline would bury the
timing events replay is built around. Instead the series lands in its own
time-indexed store and the map reads it by replay clock.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from pydantic import BaseModel, Field

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

logger = logging.getLogger(__name__)

# Provider units are 1/10 m. A lap has to leave the start/finish area by 300 m
# before a return inside 80 m counts as closing the loop.
LAP_MIN_DEPARTURE = 3_000.0
LAP_CLOSE_RADIUS = 800.0
GEOMETRY_SIMPLIFY_TOLERANCE = 25.0
# Below this a "trace" is a parked car or a pit box, not a circuit.
MIN_TRACK_TRACE_POINTS = 20


class LocationProvider(Protocol):
    async def location(self, **filters: Any) -> list[dict[str, Any]]: ...

    async def sessions(self, **filters: Any) -> list[dict[str, Any]]: ...


class LocationRepository(Protocol):
    async def bulk_insert(
        self,
        session_key: str,
        samples: list[DriverLocationSample],
        *,
        source: str = "historical",
        chunk_size: int = 1000,
    ) -> int: ...

    async def count(self, session_key: str) -> int: ...

    async def time_range(self, session_key: str) -> tuple[datetime | None, datetime | None]: ...

    async def driver_numbers(self, session_key: str) -> list[int]: ...

    async def latest_per_driver(
        self, session_key: str, *, at: datetime | None = None
    ) -> list[DriverLocationSample]: ...

    async def window(
        self,
        session_key: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        driver_number: int | None = None,
        limit: int = 20_000,
    ) -> list[DriverLocationSample]: ...

    async def sample_points(
        self, session_key: str, limit: int = 40_000
    ) -> list[tuple[float, float]]: ...

    async def save_geometry(self, geometry: SessionTrackGeometry) -> None: ...

    async def get_geometry(self, session_key: str) -> SessionTrackGeometry | None: ...


class LocationIngestionSummary(BaseModel):
    session_key: str
    windows_requested: int = 0
    windows_failed: int = 0
    provider_rows: int = 0
    rejected_rows: int = 0
    stored_samples: int = 0
    total_samples: int = 0
    drivers: list[int] = Field(default_factory=list)
    first_sample_at: datetime | None = None
    last_sample_at: datetime | None = None
    bounds: TrackBounds | None = None
    track_points: int = 0


class LocationUnavailableError(RuntimeError):
    """The provider exposes no usable location data for this session."""


def parse_location_rows(rows: Iterable[dict[str, Any]]) -> tuple[list[DriverLocationSample], int]:
    """Normalize provider rows, returning (samples, rejected_count).

    Field names come straight from the observed OpenF1 payload
    (``date``/``driver_number``/``x``/``y``/``z``); nothing is guessed. Values
    arrive as JSON numbers and are coerced to float exactly once, here.
    """

    samples: list[DriverLocationSample] = []
    rejected = 0
    for row in rows:
        driver_number = _optional_int(row.get("driver_number"))
        sample_time = _parse_time(row.get("date"))
        if driver_number is None or sample_time is None:
            rejected += 1
            continue
        if not (is_valid_coordinate(row.get("x")) and is_valid_coordinate(row.get("y"))):
            rejected += 1
            continue
        raw_z = row.get("z")
        z = float(raw_z) if is_valid_coordinate(raw_z) else None
        x = float(row["x"])
        y = float(row["y"])
        if not is_transmitting(x, y, z):
            rejected += 1
            continue
        samples.append(
            DriverLocationSample(
                driver_number=driver_number,
                x=x,
                y=y,
                z=z,
                sample_time=sample_time,
            )
        )
    return samples, rejected


def downsample(
    samples: list[DriverLocationSample],
    interval_ms: int,
) -> list[DriverLocationSample]:
    """Thin each driver's series independently to at most one fix per interval."""

    if interval_ms <= 0:
        return sorted(samples, key=lambda sample: (sample.sample_time, sample.driver_number))
    interval = timedelta(milliseconds=interval_ms)
    kept: list[DriverLocationSample] = []
    last_kept: dict[int, datetime] = {}
    for sample in sorted(samples, key=lambda item: (item.sample_time, item.driver_number)):
        previous = last_kept.get(sample.driver_number)
        if previous is not None and sample.sample_time - previous < interval:
            continue
        last_kept[sample.driver_number] = sample.sample_time
        kept.append(sample)
    return kept


def close_single_lap(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Cut a continuous trace down to one circuit lap when one is present."""

    if len(points) < 3:
        return list(points)
    origin = points[0]
    departed = False
    for index in range(1, len(points)):
        distance = math.dist(origin, points[index])
        if not departed:
            departed = distance > LAP_MIN_DEPARTURE
            continue
        if distance <= LAP_CLOSE_RADIUS:
            return points[: index + 1]
    return list(points)


def find_lap_window(
    samples: list[DriverLocationSample],
    *,
    max_lap_samples: int = 400,
    stride: int = 15,
) -> tuple[datetime, datetime] | None:
    """Find a time range in which this driver completes one closed lap.

    Searching the stored series is what makes geometry work for practice and
    qualifying, where cars spend most of the session stationary in the garage.
    """

    ordered = sorted(samples, key=lambda sample: sample.sample_time)
    points = [(sample.x, sample.y) for sample in ordered]
    for start in range(0, max(1, len(points) - MIN_TRACK_TRACE_POINTS), stride):
        segment = points[start : start + max_lap_samples]
        if len(segment) < MIN_TRACK_TRACE_POINTS:
            break
        lap = close_single_lap(segment)
        if len(lap) < len(segment) and len(lap) >= MIN_TRACK_TRACE_POINTS:
            return ordered[start].sample_time, ordered[start + len(lap) - 1].sample_time
    return None


def build_track_geometry(
    session_key: str,
    lap_points: list[tuple[float, float]],
    session_points: list[tuple[float, float]],
    *,
    source_driver_number: int | None = None,
    sample_count: int = 0,
) -> SessionTrackGeometry | None:
    """Derive the outline and the viewport extent from the same sample space.

    The viewport union keeps the racing line fully framed while still showing
    pit-lane and garage positions, without letting one stray fix decide the
    scale. Raw driver samples are never modified to suit the viewport.
    """

    path = simplify_path(lap_points, GEOMETRY_SIMPLIFY_TOLERANCE) if lap_points else []
    if len(path) > 2 and 0 < math.dist(path[0], path[-1]) <= LAP_CLOSE_RADIUS:
        # A circuit is a closed loop; the trace ends a sample short of its own
        # start, so joining the ends removes a break the track does not have.
        path.append(path[0])
    bounds = bounds_from_points(path) or percentile_bounds(session_points)
    if bounds is None:
        return None
    bounds = bounds.union(percentile_bounds(session_points, lower=0.005, upper=0.995))
    if bounds.width <= 0 or bounds.height <= 0:
        return None
    return SessionTrackGeometry(
        session_key=session_key,
        bounds=bounds,
        path=path,
        source_driver_number=source_driver_number,
        sample_count=sample_count,
    )


class LocationIngestionService:
    """Fetch a whole session's location series in bounded provider windows."""

    def __init__(
        self,
        *,
        client: LocationProvider,
        repository: LocationRepository,
        sample_interval_ms: int = 1000,
        fetch_window_seconds: int = 120,
        max_samples_per_session: int = 250_000,
        window_retry_passes: int = 2,
    ) -> None:
        self.window_retry_passes = max(0, window_retry_passes)
        self.client = client
        self.repository = repository
        self.sample_interval_ms = sample_interval_ms
        self.fetch_window_seconds = max(10, fetch_window_seconds)
        self.max_samples_per_session = max_samples_per_session

    async def ingest_session(
        self,
        session_key: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        max_minutes: int | None = None,
    ) -> LocationIngestionSummary:
        window_start, window_end = await self.resolve_window(session_key, start, end)
        if max_minutes is not None:
            window_end = min(window_end, window_start + timedelta(minutes=max_minutes))

        summary = LocationIngestionSummary(session_key=session_key)
        step = timedelta(seconds=self.fetch_window_seconds)
        pending: list[tuple[datetime, datetime]] = []
        cursor = window_start
        while cursor < window_end:
            pending.append((cursor, min(cursor + step, window_end)))
            cursor = min(cursor + step, window_end)

        # OpenF1 intermittently 500s on high-frequency windows even after the
        # client's own retries. Sweeping the failures again leaves far fewer
        # holes in the replay series than accepting the first answer.
        for attempt in range(self.window_retry_passes + 1):
            if not pending:
                break
            failed: list[tuple[datetime, datetime]] = []
            for chunk_start, chunk_end in pending:
                if summary.stored_samples >= self.max_samples_per_session:
                    break
                summary.windows_requested += 1
                try:
                    rows = await self.client.location(
                        session_key=session_key,
                        # Both bounds are strict, so windows overlap by a second
                        # to stay gapless. Re-fetched rows are dropped by the
                        # unique constraint on (session, driver, sample_time).
                        **_date_window(chunk_start - timedelta(seconds=1), chunk_end),
                    )
                except Exception as exc:
                    failed.append((chunk_start, chunk_end))
                    logger.warning(
                        "location_window_failed session_key=%s window_start=%s attempt=%s error=%s",
                        session_key,
                        chunk_start.isoformat(timespec="seconds"),
                        attempt + 1,
                        type(exc).__name__,
                    )
                    continue
                summary.provider_rows += len(rows)
                samples, rejected = parse_location_rows(rows)
                summary.rejected_rows += rejected
                reduced = downsample(samples, self.sample_interval_ms)
                summary.stored_samples += await self.repository.bulk_insert(session_key, reduced)
            pending = failed
        summary.windows_failed = len(pending)

        summary.total_samples = await self.repository.count(session_key)
        if summary.total_samples == 0:
            logger.info(
                "location_lookup session_key=%s rows=0 drivers=0 windows=%s failed_windows=%s",
                session_key,
                summary.windows_requested,
                summary.windows_failed,
            )
            raise LocationUnavailableError(
                f"OpenF1 returned no usable location samples for session {session_key}"
            )

        summary.drivers = await self.repository.driver_numbers(session_key)
        summary.first_sample_at, summary.last_sample_at = await self.repository.time_range(
            session_key
        )
        geometry = await self.rebuild_geometry(session_key, window_start, window_end)
        if geometry is not None:
            summary.bounds = geometry.bounds
            summary.track_points = len(geometry.path)

        logger.info(
            "location_lookup session_key=%s rows=%s drivers=%s first_timestamp=%s "
            "last_timestamp=%s windows=%s failed_windows=%s track_points=%s",
            session_key,
            summary.total_samples,
            len(summary.drivers),
            summary.first_sample_at.isoformat() if summary.first_sample_at else None,
            summary.last_sample_at.isoformat() if summary.last_sample_at else None,
            summary.windows_requested,
            summary.windows_failed,
            summary.track_points,
        )
        return summary

    async def rebuild_geometry(
        self,
        session_key: str,
        window_start: datetime,
        window_end: datetime,
    ) -> SessionTrackGeometry | None:
        """Trace the outline from one driver's native-resolution lap.

        The stored series is thinned for replay, which is too coarse for
        corners, so the lap is re-fetched at provider resolution. Which lap is
        found in the stored series rather than guessed from a time offset: in
        qualifying and practice a car picked at an arbitrary moment is usually
        sitting in the garage, and a stationary trace is not a circuit.
        """

        drivers = await self.repository.driver_numbers(session_key)
        if not drivers:
            return None
        ranked = await self._drivers_by_sample_count(session_key, drivers)
        reference = ranked[0] if ranked else drivers[0]
        lap_points: list[tuple[float, float]] = []
        fallback_points: list[tuple[float, float]] = []

        for driver in ranked[:5]:
            series = await self.repository.window(session_key, driver_number=driver, limit=20_000)
            fallback_points = fallback_points or close_single_lap(_trace_for_driver(series))
            lap_window = find_lap_window(series)
            if lap_window is None:
                continue
            try:
                rows = await self.client.location(
                    session_key=session_key,
                    driver_number=driver,
                    **_date_window(
                        lap_window[0] - timedelta(seconds=1),
                        lap_window[1] + timedelta(seconds=1),
                    ),
                )
            except Exception as exc:
                logger.warning(
                    "location_geometry_window_failed session_key=%s driver=%s error=%s",
                    session_key,
                    driver,
                    type(exc).__name__,
                )
                continue
            samples, _ = parse_location_rows(rows)
            samples.sort(key=lambda sample: sample.sample_time)
            candidate = close_single_lap(_trace_for_driver(samples))
            if len(candidate) >= MIN_TRACK_TRACE_POINTS:
                lap_points = candidate
                reference = driver
                break

        session_points = await self.repository.sample_points(session_key)
        geometry = build_track_geometry(
            session_key,
            lap_points or fallback_points,
            session_points,
            source_driver_number=reference,
            # The full stored count, not the capped slice used for bounds: this
            # is what the debug panel reports as the session's sample volume.
            sample_count=await self.repository.count(session_key),
        )
        if geometry is not None:
            await self.repository.save_geometry(geometry)
        return geometry

    async def _drivers_by_sample_count(self, session_key: str, drivers: list[int]) -> list[int]:
        counts: dict[int, int] = {}
        for driver in drivers:
            samples = await self.repository.window(session_key, driver_number=driver, limit=20_000)
            counts[driver] = len(samples)
        return sorted(drivers, key=lambda driver: (-counts[driver], driver))

    async def resolve_window(
        self,
        session_key: str,
        start: datetime | None,
        end: datetime | None,
    ) -> tuple[datetime, datetime]:
        if start is not None and end is not None:
            return _aware(start), _aware(end)
        rows = await self.client.sessions(session_key=session_key)
        if not rows:
            raise LocationUnavailableError(f"Unknown provider session {session_key}")
        row = rows[0]
        resolved_start = start or _parse_time(row.get("date_start"))
        resolved_end = end or _parse_time(row.get("date_end"))
        if resolved_start is None or resolved_end is None or resolved_end <= resolved_start:
            raise LocationUnavailableError(
                f"Provider session {session_key} has no usable time window"
            )
        return _aware(resolved_start), _aware(resolved_end)


class SessionLocationService:
    """Read views over persisted location, shared by live and replay."""

    def __init__(self, repository: LocationRepository) -> None:
        self.repository = repository

    async def latest(
        self,
        session_key: str,
        *,
        at: datetime | None = None,
    ) -> list[DriverLocationSample]:
        return await self.repository.latest_per_driver(session_key, at=at)

    async def window(
        self,
        session_key: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        driver_number: int | None = None,
        limit: int = 20_000,
    ) -> list[DriverLocationSample]:
        return await self.repository.window(
            session_key,
            since=since,
            until=until,
            driver_number=driver_number,
            limit=limit,
        )

    async def geometry(self, session_key: str) -> SessionTrackGeometry | None:
        return await self.repository.get_geometry(session_key)

    async def sample_count(self, session_key: str) -> int:
        return await self.repository.count(session_key)

    async def time_range(self, session_key: str) -> tuple[datetime | None, datetime | None]:
        return await self.repository.time_range(session_key)

    async def record_live_samples(
        self,
        session_key: str,
        samples: list[DriverLocationSample],
    ) -> int:
        """Persist live provider fixes so reconnects get an instant snapshot."""

        return await self.repository.bulk_insert(session_key, samples, source="live")


class LiveLocationRecorder:
    """Persist live provider fixes as they flow through the event pipeline.

    Without this a browser connecting mid-session would have to wait for the
    next fix from every car before any marker appeared, and nothing would
    survive a process restart.
    """

    def __init__(self, service: SessionLocationService) -> None:
        self.service = service

    async def consume(self, event: Any) -> None:
        payload = getattr(event, "payload", None)
        event_type = getattr(getattr(event, "event_type", None), "value", None)
        if event_type != "LOCATION_SAMPLE" or not isinstance(payload, dict):
            return
        samples, _ = parse_location_rows([{**payload, "date": payload.get("date")}])
        if not samples:
            return
        try:
            await self.service.record_live_samples(str(event.session_key), samples)
        except Exception as exc:
            # Location is an enhancement layer. Timing, race control and the
            # room conversation must not fail because the map store is down.
            logger.error(
                "live_location_persist_failed session_key=%s error=%s",
                getattr(event, "session_key", None),
                type(exc).__name__,
            )


def _trace_for_driver(samples: list[DriverLocationSample]) -> list[tuple[float, float]]:
    return [(sample.x, sample.y) for sample in sorted(samples, key=lambda item: item.sample_time)]


def _date_window(start: datetime, end: datetime) -> dict[str, str]:
    """Provider-compatible date filters for a half-open window.

    OpenF1 only implements the strict comparisons: ``date>=`` and ``date<=``
    are rejected (404, or 500 when paired), so the window is expressed with
    ``date>``/``date<``. Timestamps must also be whole seconds without an
    offset -- the parser rejects both microseconds and a ``+00:00`` suffix.
    Provider dates are UTC, so dropping the offset is lossless.
    """

    return {
        "date>": _provider_timestamp(start),
        "date<": _provider_timestamp(end),
    }


def _provider_timestamp(value: datetime) -> str:
    return _aware(value).astimezone(UTC).replace(tzinfo=None).isoformat(timespec="seconds")


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_time(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return _aware(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _aware(parsed)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)
