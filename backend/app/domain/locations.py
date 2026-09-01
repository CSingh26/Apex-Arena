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
