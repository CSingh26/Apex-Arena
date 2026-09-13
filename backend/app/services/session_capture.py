# SPDX-License-Identifier: AGPL-3.0-only
"""Bounded operational intake, explicitly independent of sporting completion."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal


@dataclass(frozen=True)
class CaptureDecision:
    eligible: bool
    deadline: datetime
    reason: Literal["before_start", "watching", "terminal", "expired_unconfirmed", "cancelled"]


def capture_policy(
    scheduled_start: datetime,
    now: datetime,
    *,
    confirmed_terminal: bool = False,
    cancelled: bool = False,
    window_seconds: int = 43200,
) -> CaptureDecision:
    start = (
        scheduled_start.replace(tzinfo=UTC) if scheduled_start.tzinfo is None else scheduled_start
    )
    current = now.replace(tzinfo=UTC) if now.tzinfo is None else now
    deadline = start + timedelta(seconds=window_seconds)
    reason = (
        "terminal"
        if confirmed_terminal
        else "cancelled"
        if cancelled
        else "before_start"
        if current < start
        else "expired_unconfirmed"
        if current >= deadline
        else "watching"
    )
    return CaptureDecision(reason == "watching", deadline, reason)
