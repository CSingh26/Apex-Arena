# SPDX-License-Identifier: AGPL-3.0-only
"""Provider-neutral driver track position domain.

OpenF1 publishes car positions in a circuit-local Cartesian frame (units of
1/10 m, Y increasing "up" on the provider plot, Z as elevation). Nothing here
is geographic: no latitude, no longitude, no browser geolocation. The whole
pipeline keeps these raw provider units and defers every viewport decision to
the shared projection in the frontend.
"""

from __future__ import annotations

import math
import statistics
from datetime import datetime

from pydantic import BaseModel, Field

# Provider samples live well inside +/-40000 in practice. The guard exists to
# reject corrupt rows, not to clip legitimate circuit geometry.
COORDINATE_LIMIT = 100_000


class DriverLocationSample(BaseModel):
    """One provider position fix for one car, in raw provider coordinates."""

    driver_number: int
    x: float
    y: float
    z: float | None = None
    sample_time: datetime


class TrackBounds(BaseModel):
    """Axis-aligned extent used to build the map viewport, never to clip data."""

    min_x: float
    max_x: float
    min_y: float
    max_y: float

    @property
    def width(self) -> float:
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        return self.max_y - self.min_y

    def union(self, other: TrackBounds | None) -> TrackBounds:
        if other is None:
            return self
        return TrackBounds(
            min_x=min(self.min_x, other.min_x),
            max_x=max(self.max_x, other.max_x),
            min_y=min(self.min_y, other.min_y),
            max_y=max(self.max_y, other.max_y),
        )


class SessionTrackGeometry(BaseModel):
    """Circuit outline traced from the same provider samples the cars use.

    Deriving the outline from driver telemetry rather than an external circuit
    dataset is what guarantees markers and track share one coordinate space.
    """

    session_key: str
    bounds: TrackBounds
    path: list[tuple[float, float]] = Field(default_factory=list)
    source_driver_number: int | None = None
    sample_count: int = 0


def is_valid_coordinate(value: object) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and abs(number) <= COORDINATE_LIMIT


def is_transmitting(x: float, y: float, z: float | None) -> bool:
    """OpenF1 emits an exact (0, 0, 0) fix while a car is not transmitting.

    Those rows are real provider output, not corrupt data, but they are not a
    place on the circuit. Keeping them would drag automatic viewport bounds
    toward the origin and park phantom cars there.
    """

    return not (x == 0 and y == 0 and (z is None or z == 0))


def bounds_from_points(points: list[tuple[float, float]]) -> TrackBounds | None:
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return TrackBounds(min_x=min(xs), max_x=max(xs), min_y=min(ys), max_y=max(ys))


def percentile_bounds(
    points: list[tuple[float, float]],
    *,
    lower: float = 0.02,
    upper: float = 0.98,
) -> TrackBounds | None:
    """Outlier-resistant extent.

    A single garage or recovery-truck sample sitting far off the circuit would
    otherwise shrink the whole track into a corner of the viewport.
    """

    if not points:
        return None
    if len(points) < 50:
        return bounds_from_points(points)
    xs = sorted(point[0] for point in points)
    ys = sorted(point[1] for point in points)

    def pick(values: list[float], fraction: float) -> float:
        index = min(len(values) - 1, max(0, int(round(fraction * (len(values) - 1)))))
        return values[index]

    return TrackBounds(
        min_x=pick(xs, lower),
        max_x=pick(xs, upper),
        min_y=pick(ys, lower),
        max_y=pick(ys, upper),
    )


# A single lap of the retained series is thinned to roughly 1 Hz for replay,
# which is about 60 m between fixes at racing speed: far too coarse to describe
# a corner. Every retained sample from every driver lands at a different point
# on each lap, so their union describes the same circuit far more densely than
# any one trace. Aggregating them recovers detail that is genuinely observed
# rather than interpolated.
CENTERLINE_CORRIDOR = 300.0
"""Half-width, in provider units, of the band kept around the reference lap.

30 m covers the track plus a margin while leaving the pit lane outside it.
"""

CENTERLINE_BIN_LENGTH = 60.0
"""Lap distance, in provider units, represented by one aggregated point."""

CENTERLINE_MIN_BIN_SAMPLES = 3
"""Below this a bin is one car's excursion, not evidence of where the track is."""

CENTERLINE_MIN_COVERAGE = 0.9
"""Reject a partial aggregate outright; a gap would cut a corner silently."""

CENTERLINE_MAX_POINTS = 2_000


def _loop_segments(
    skeleton: list[tuple[float, float]],
) -> tuple[list[tuple[tuple[float, float], tuple[float, float], float, float]], float]:
    """Measure arc length along the reference lap, dropping repeated fixes."""
    segments: list[tuple[tuple[float, float], tuple[float, float], float, float]] = []
    travelled = 0.0
    for start, end in zip(skeleton, skeleton[1:], strict=False):
        length = math.dist(start, end)
        if length <= 0:
            continue
        segments.append((start, end, travelled, length))
        travelled += length
    return segments, travelled


def _project_onto_loop(
    point: tuple[float, float],
    segments: list[tuple[tuple[float, float], tuple[float, float], float, float]],
    grid: dict[tuple[int, int], set[int]],
    cell: float,
) -> tuple[float, float] | None:
    """Return (distance from the lap, distance along it) for one sample."""
    cx, cy = int(point[0] // cell), int(point[1] // cell)
    best: tuple[float, float] | None = None
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for index in grid.get((cx + dx, cy + dy), ()):
                start, end, offset, length = segments[index]
                vx, vy = end[0] - start[0], end[1] - start[1]
                along = ((point[0] - start[0]) * vx + (point[1] - start[1]) * vy) / (
                    length * length
                )
                along = min(1.0, max(0.0, along))
                nearest = (start[0] + vx * along, start[1] + vy * along)
                distance = math.dist(point, nearest)
                if best is None or distance < best[0]:
                    best = (distance, offset + length * along)
    return best


def aggregate_centerline(
    skeleton: list[tuple[float, float]],
    cloud: list[tuple[float, float]],
    *,
    corridor: float = CENTERLINE_CORRIDOR,
    bin_length: float = CENTERLINE_BIN_LENGTH,
    min_bin_samples: int = CENTERLINE_MIN_BIN_SAMPLES,
    min_coverage: float = CENTERLINE_MIN_COVERAGE,
) -> list[tuple[float, float]] | None:
    """Trace the circuit from every retained sample, ordered by the reference lap.

    The reference lap supplies only the running order; each published point is
    the median observed position within one short slice of lap distance, so the
    outline is measured rather than smoothed into shape. Returns None when the
    samples do not cover the whole lap, because a partial aggregate would cut a
    corner without saying so.
    """
    if len(skeleton) < 3 or not cloud:
        return None
    segments, total = _loop_segments(skeleton)
    if not segments or total <= 0:
        return None

    cell = max(corridor, 1.0)
    grid: dict[tuple[int, int], set[int]] = {}
    for index, (start, end, _offset, length) in enumerate(segments):
        steps = max(1, int(length // cell) + 1)
        for step in range(steps + 1):
            along = step / steps
            px = start[0] + (end[0] - start[0]) * along
            py = start[1] + (end[1] - start[1]) * along
            grid.setdefault((int(px // cell), int(py // cell)), set()).add(index)

    bins: dict[int, list[tuple[float, float]]] = {}
    for point in cloud:
        located = _project_onto_loop(point, segments, grid, cell)
        if located is None or located[0] > corridor:
            continue
        bins.setdefault(int(located[1] // bin_length), []).append(point)

    expected = int(total // bin_length) + 1
    usable = {key: values for key, values in bins.items() if len(values) >= min_bin_samples}
    if expected <= 0 or len(usable) < expected * min_coverage:
        return None

    centerline = [
        (
            statistics.median(point[0] for point in usable[key]),
            statistics.median(point[1] for point in usable[key]),
        )
        for key in sorted(usable)
    ]
    if len(centerline) < 3:
        return None
    centerline.append(centerline[0])
    return centerline


def simplify_path(
    points: list[tuple[float, float]],
    tolerance: float,
    max_points: int = 600,
) -> list[tuple[float, float]]:
    """Ramer-Douglas-Peucker, then a uniform thin if the trace is still dense."""

    if len(points) <= 2:
        return list(points)
    simplified = _rdp(points, max(tolerance, 1e-6))
    if len(simplified) <= max_points:
        return simplified
    step = math.ceil(len(simplified) / max_points)
    thinned = simplified[::step]
    if thinned[-1] != simplified[-1]:
        thinned.append(simplified[-1])
    return thinned


def _rdp(points: list[tuple[float, float]], tolerance: float) -> list[tuple[float, float]]:
    stack = [(0, len(points) - 1)]
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        index, distance = _furthest(points, start, end)
        if distance > tolerance:
            keep[index] = True
            stack.append((start, index))
            stack.append((index, end))
    return [point for point, keeper in zip(points, keep, strict=True) if keeper]


def _furthest(points: list[tuple[float, float]], start: int, end: int) -> tuple[int, float]:
    ax, ay = points[start]
    bx, by = points[end]
    dx, dy = bx - ax, by - ay
    span = math.hypot(dx, dy)
    best_index = start
    best_distance = -1.0
    for index in range(start + 1, end):
        px, py = points[index]
        if span == 0:
            distance = math.hypot(px - ax, py - ay)
        else:
            distance = abs(dy * px - dx * py + bx * ay - by * ax) / span
        if distance > best_distance:
            best_index, best_distance = index, distance
    return best_index, best_distance
