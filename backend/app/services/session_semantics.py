# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import re
from typing import Any

from app.domain.rooms import SessionType

# Backwards-compatible service-level spelling.  ``SessionType`` is the single
# canonical enum; this alias keeps the matching helpers readable without
# introducing a competing identity type.
CompetitiveSessionType = SessionType


_SESSION_ALIASES: dict[str, CompetitiveSessionType] = {
    "qualifying": CompetitiveSessionType.QUALIFYING,
    "qualification": CompetitiveSessionType.QUALIFYING,
    "qualifying session": CompetitiveSessionType.QUALIFYING,
    "sprint qualifying": CompetitiveSessionType.SPRINT_QUALIFYING,
    "sprint qualification": CompetitiveSessionType.SPRINT_QUALIFYING,
    "sprint shootout": CompetitiveSessionType.SPRINT_QUALIFYING,
    "sprint qualifying session": CompetitiveSessionType.SPRINT_QUALIFYING,
    "sprint": CompetitiveSessionType.SPRINT,
    "sprint race": CompetitiveSessionType.SPRINT,
    "race": CompetitiveSessionType.RACE,
    "grand prix": CompetitiveSessionType.RACE,
}

SESSION_DISPLAY_NAMES: dict[CompetitiveSessionType, str] = {
    CompetitiveSessionType.QUALIFYING: "Qualifying",
    CompetitiveSessionType.SPRINT_QUALIFYING: "Sprint Qualifying",
    CompetitiveSessionType.SPRINT: "Sprint",
    CompetitiveSessionType.RACE: "Race",
}


def _words(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def normalize_session_type(value: object) -> CompetitiveSessionType | None:
    if isinstance(value, CompetitiveSessionType):
        return value
    normalized = _words(value)
    direct = _SESSION_ALIASES.get(normalized)
    if direct is not None:
        return direct
    # Providers occasionally append a year, day, or status to the canonical
    # label.  Match the distinctive Sprint terms first so they never collapse
    # into main qualifying or the Sunday race.
    if "sprint" in normalized and ("qual" in normalized or "shootout" in normalized):
        return CompetitiveSessionType.SPRINT_QUALIFYING
    if normalized.startswith("sprint"):
        return CompetitiveSessionType.SPRINT
    if normalized.startswith("qual"):
        return CompetitiveSessionType.QUALIFYING
    if normalized in {"main race", "feature race"}:
        return CompetitiveSessionType.RACE
    return None


def session_display_name(value: object) -> str:
    normalized = normalize_session_type(value)
    return SESSION_DISPLAY_NAMES.get(normalized, str(value or "Session"))


def is_qualifying_session(value: object) -> bool:
    return normalize_session_type(value) in {
        CompetitiveSessionType.QUALIFYING,
        CompetitiveSessionType.SPRINT_QUALIFYING,
    }


def session_phases(value: object) -> tuple[str, ...]:
    session_type = normalize_session_type(value)
    if session_type == CompetitiveSessionType.QUALIFYING:
        return ("Q1", "Q2", "Q3")
    if session_type == CompetitiveSessionType.SPRINT_QUALIFYING:
        return ("SQ1", "SQ2", "SQ3")
    return ()


def normalize_qualifying_phase(value: Any, session_type: object) -> str | None:
    """Return Q1/Q2/Q3 or SQ1/SQ2/SQ3 without inventing a boundary.

    A phase is emitted only when the provider supplied an explicit phase or a
    phase-indexed result.  Elapsed time alone is intentionally not used.
    """

    if value is None or isinstance(value, bool):
        return None
    normalized_type = normalize_session_type(session_type)
    if normalized_type not in {
        CompetitiveSessionType.QUALIFYING,
        CompetitiveSessionType.SPRINT_QUALIFYING,
    }:
        return None
    if isinstance(value, int | float):
        phase_number = int(value)
    else:
        text = _words(value)
        match = re.search(r"(?:sq|q|phase|qualifying)\s*([123])\b", text)
        if match is None and text in {"1", "2", "3"}:
            match = re.search(r"([123])", text)
        if match is None:
            return None
        phase_number = int(match.group(1))
    if phase_number not in {1, 2, 3}:
        return None
    prefix = "SQ" if normalized_type == CompetitiveSessionType.SPRINT_QUALIFYING else "Q"
    return f"{prefix}{phase_number}"


def phase_result_rows(payload: dict[str, Any], session_type: object) -> list[dict[str, Any]]:
    """Expand OpenF1 phase arrays while preserving uncertainty/missing values."""

    phases = session_phases(session_type)
    if not phases:
        return []
    durations = payload.get("duration")
    gaps = payload.get("gap_to_leader")
    if not isinstance(durations, list) and not isinstance(gaps, list):
        return []
    duration_values = durations if isinstance(durations, list) else []
    gap_values = gaps if isinstance(gaps, list) else []
    count = min(3, max(len(duration_values), len(gap_values)))
    return [
        {
            "phase": phases[index],
            "best_lap": duration_values[index] if index < len(duration_values) else None,
            "gap_to_leader": gap_values[index] if index < len(gap_values) else None,
        }
        for index in range(count)
    ]
