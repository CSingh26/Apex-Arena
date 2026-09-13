# SPDX-License-Identifier: AGPL-3.0-only
"""Tracing the circuit from every retained sample rather than one sparse lap.

A retained lap is thinned to roughly 1 Hz, which is about 60 m between fixes at
racing speed, so its corners are described by a handful of points. These tests
use a synthetic circle because its true shape is known exactly, which makes the
recovered outline measurable rather than merely plausible.
"""

from __future__ import annotations

import math

import pytest

from app.domain.locations import (
    CENTERLINE_CORRIDOR,
    aggregate_centerline,
)

CENTRE = (5_000.0, 5_000.0)
RADIUS = 9_000.0


def ring(count: int, *, radius: float = RADIUS, offset: float = 0.0) -> list[tuple[float, float]]:
    """Points around the synthetic circuit, at a given radial offset."""
    return [
        (
            CENTRE[0] + (radius + offset) * math.cos(2 * math.pi * index / count),
            CENTRE[1] + (radius + offset) * math.sin(2 * math.pi * index / count),
        )
        for index in range(count)
    ]


def closed(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    return [*points, points[0]]


def outline_error(path: list[tuple[float, float]]) -> float:
    """Mean distance from the true circuit to the traced outline.

    Vertex position alone does not measure fidelity: a coarse polygon can have
    every vertex exactly on the circle while its edges cut every corner. What
    matters is how far the real track sits from the rendered line.
    """
    total = 0.0
    probes = 2_000
    for index in range(probes):
        angle = 2 * math.pi * index / probes
        truth = (CENTRE[0] + RADIUS * math.cos(angle), CENTRE[1] + RADIUS * math.sin(angle))
        nearest = min(
            _distance_to_segment(truth, path[i], path[i + 1]) for i in range(len(path) - 1)
        )
        total += nearest
    return total / probes


def _distance_to_segment(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    vx, vy = end[0] - start[0], end[1] - start[1]
    length_squared = vx * vx + vy * vy
    if length_squared == 0:
        return math.dist(point, start)
    along = ((point[0] - start[0]) * vx + (point[1] - start[1]) * vy) / length_squared
    along = min(1.0, max(0.0, along))
    return math.dist(point, (start[0] + vx * along, start[1] + vy * along))


def dense_cloud(laps: int = 40, per_lap: int = 400) -> list[tuple[float, float]]:
    """Many laps, each sampled at a different rotational phase.

    This is the property the aggregate exploits: no single lap resolves a
    corner, but successive laps land between one another's fixes.
    """
    cloud: list[tuple[float, float]] = []
    for lap in range(laps):
        phase = 2 * math.pi * lap / (laps * per_lap)
        for index in range(per_lap):
            angle = 2 * math.pi * index / per_lap + phase
            cloud.append(
                (CENTRE[0] + RADIUS * math.cos(angle), CENTRE[1] + RADIUS * math.sin(angle))
            )
    return cloud


def test_the_aggregate_recovers_the_circuit_a_sparse_lap_only_approximates():
    # 60 fixes is about what one 1 Hz lap carries.
    skeleton = closed(ring(60))
    centerline = aggregate_centerline(skeleton, dense_cloud())

    assert centerline is not None
    assert len(centerline) > len(skeleton) * 5, "the aggregate must add real detail"
    # A 60-point polygon cuts every corner; the aggregate should not.
    assert outline_error(centerline) < outline_error(skeleton) / 20


def test_every_published_point_is_an_observed_position():
    """The outline is measured, not smoothed into shape."""
    skeleton = closed(ring(60))
    centerline = aggregate_centerline(skeleton, dense_cloud())

    assert centerline is not None
    # Medians of samples that all sit on the circle stay on the circle; nothing
    # is invented between them.
    assert all(abs(math.dist(point, CENTRE) - RADIUS) < 1.0 for point in centerline)


def test_the_traced_outline_is_a_closed_loop():
    centerline = aggregate_centerline(closed(ring(60)), dense_cloud())
    assert centerline is not None
    assert centerline[0] == centerline[-1]


def test_the_same_samples_always_trace_the_same_outline():
    """Replay parity depends on this being deterministic."""
    skeleton = closed(ring(60))
    cloud = dense_cloud()
    assert aggregate_centerline(skeleton, cloud) == aggregate_centerline(skeleton, cloud)


def test_samples_beyond_the_corridor_are_excluded():
    """A pit lane runs alongside the circuit and is not part of it."""
    skeleton = closed(ring(60))
    # A parallel lane well outside the corridor.
    pit_lane = ring(4_000, offset=1_200.0)
    centerline = aggregate_centerline(skeleton, [*dense_cloud(), *pit_lane])

    assert centerline is not None
    # The far lane never pulls a published point off the racing line.
    assert all(abs(math.dist(point, CENTRE) - RADIUS) < 1.0 for point in centerline)


def test_a_lone_excursion_cannot_move_the_track():
    """One car running wide is not evidence of where the circuit is."""
    skeleton = closed(ring(60))
    cloud = dense_cloud()
    # Two fixes well off line, but still inside the corridor.
    excursion = [
        (
            CENTRE[0] + (RADIUS + offset) * math.cos(0.5),
            CENTRE[1] + (RADIUS + offset) * math.sin(0.5),
        )
        for offset in (250.0, 260.0)
    ]

    clean = aggregate_centerline(skeleton, cloud)
    disturbed = aggregate_centerline(skeleton, [*cloud, *excursion])
    assert clean is not None and disturbed is not None
    assert len(clean) == len(disturbed)

    # Taking the median of each slice means a pair of stray fixes cannot drag
    # the published line towards them.
    worst = max(math.dist(a, b) for a, b in zip(clean, disturbed, strict=True))
    assert worst < 10.0, f"excursion moved the outline by {worst:.1f} units"


def test_partial_coverage_is_refused_rather_than_cutting_a_corner():
    """Half a lap of samples must not be published as a whole circuit."""
    skeleton = closed(ring(60))
    half = [point for point in dense_cloud() if point[1] >= CENTRE[1]]
    assert aggregate_centerline(skeleton, half) is None


@pytest.mark.parametrize(
    ("skeleton", "cloud"),
    [
        ([], []),
        (closed(ring(60)), []),
        ([(0.0, 0.0), (1.0, 1.0)], [(0.0, 0.0)]),
        ([(0.0, 0.0), (0.0, 0.0), (0.0, 0.0)], [(0.0, 0.0)]),
    ],
)
def test_degenerate_input_is_refused(skeleton, cloud):
    assert aggregate_centerline(skeleton, cloud) is None


def test_a_thin_cloud_cannot_certify_a_circuit():
    """Below the per-slice minimum there is no evidence, only a single trace."""
    skeleton = closed(ring(60))
    assert aggregate_centerline(skeleton, ring(120)) is None


def test_corridor_is_wide_enough_for_a_real_track_and_narrower_than_a_pit_lane():
    # Provider units are 1/10 m, so this is 30 m: track width plus a margin.
    assert 200.0 <= CENTERLINE_CORRIDOR <= 400.0
