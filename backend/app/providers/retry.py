# SPDX-License-Identifier: AGPL-3.0-only
"""Finite retry delays shared by provider adapters (seconds)."""

from __future__ import annotations

import math


def bounded_retry_delay(header: str | None, fallback: float) -> float:
    try:
        requested = float(header) if header is not None else 0.0
    except ValueError:
        requested = 0.0
    if not math.isfinite(requested):
        requested = 0.0
    return min(5.0, max(0.0, requested, fallback))
