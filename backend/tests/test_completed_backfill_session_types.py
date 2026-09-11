# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from app.domain.rooms import SessionType
from app.storage.room_repository import COMPLETED_BACKFILL_SESSION_TYPES


def test_manual_backfill_covers_every_weekend_session_type() -> None:
    """Operators must be able to recover practice sessions, not only competitive ones."""

    assert set(COMPLETED_BACKFILL_SESSION_TYPES) == set(SessionType)
