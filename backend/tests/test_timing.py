# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import pytest

from app.services.timing import format_gap, format_lap_time, format_pit_stop, format_sector_time


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (79.44, "1:19.440"),
        (110.02, "1:50.020"),
        (59.9, "59.900"),
        (60, "1:00.000"),
        (61.001, "1:01.001"),
        (125.418, "2:05.418"),
    ],
)
def test_lap_time_uses_conventional_formula_one_timing(value: float, expected: str) -> None:
    assert format_lap_time(value) == expected


@pytest.mark.parametrize("value", [None, "not-a-time", -1, 0, 700.77, float("inf")])
def test_lap_time_rejects_missing_invalid_and_implausible_values(value: object) -> None:
    assert format_lap_time(value) is None


def test_timing_helpers_keep_duration_types_explicit() -> None:
    assert format_sector_time(19.04) == "19.040"
    assert format_pit_stop(2.41) == "2.410"
    assert format_gap(1.248) == "+1.248"
    assert format_lap_time(79440, unit="milliseconds") == "1:19.440"
