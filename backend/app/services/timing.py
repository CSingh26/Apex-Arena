# SPDX-License-Identifier: AGPL-3.0-only
"""Presentation-safe timing helpers for public race-room copy."""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Literal

TimingUnit = Literal["seconds", "milliseconds"]


def normalize_seconds(
    value: object, *, unit: TimingUnit = "seconds", maximum: float
) -> float | None:
    """Return a plausible positive duration in seconds; never guess an input unit."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if unit == "milliseconds":
        seconds /= 1_000
    if not math.isfinite(seconds) or seconds <= 0 or seconds > maximum:
        return None
    return seconds


def format_duration(
    value: object, *, unit: TimingUnit = "seconds", maximum: float = 600
) -> str | None:
    seconds = normalize_seconds(value, unit=unit, maximum=maximum)
    if seconds is None:
        return None
    try:
        milliseconds = int(
            (Decimal(str(seconds)) * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
    except (InvalidOperation, ValueError):
        return None
    minutes, remainder = divmod(milliseconds, 60_000)
    whole_seconds, millis = divmod(remainder, 1_000)
    return (
        f"{minutes}:{whole_seconds:02d}.{millis:03d}"
        if minutes
        else f"{whole_seconds}.{millis:03d}"
    )


def format_lap_time(value: object, *, unit: TimingUnit = "seconds") -> str | None:
    return format_duration(value, unit=unit, maximum=300)


def format_sector_time(value: object, *, unit: TimingUnit = "seconds") -> str | None:
    return format_duration(value, unit=unit, maximum=180)


def format_pit_stop(value: object, *, unit: TimingUnit = "seconds") -> str | None:
    return format_duration(value, unit=unit, maximum=120)


def format_gap(value: object, *, unit: TimingUnit = "seconds") -> str | None:
    duration = format_duration(value, unit=unit, maximum=300)
    return f"+{duration}" if duration is not None else None
