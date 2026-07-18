# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import logging
import re
import unicodedata
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.domain.models import RaceMeeting
from app.services.session_semantics import (
    CompetitiveSessionType,
    normalize_session_type,
)

logger = logging.getLogger(__name__)


class MatchConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"


class ProviderMeetingMatch(BaseModel):
    meeting_key: str | None = None
    confidence: MatchConfidence
    score: float = 0
    reason: str
    sessions: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return self.meeting_key is not None and self.confidence not in {
            MatchConfidence.UNRESOLVED,
            MatchConfidence.AMBIGUOUS,
        }


class ProviderSessionMatch(BaseModel):
    session_key: str | None = None
    meeting_key: str | None = None
    session_type: CompetitiveSessionType
    confidence: MatchConfidence
    score: float = 0
    reason: str
    session: dict[str, Any] | None = None

    @property
    def resolved(self) -> bool:
        return self.session_key is not None and self.session is not None


def _text(value: object) -> str:
    ascii_value = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", ascii_value.casefold()).strip()


def _name_text(value: object) -> str:
    words = _text(value).split()
    ignored = {"formula", "1", "f1", "grand", "prix", "gp", "the"}
    return " ".join(word for word in words if word not in ignored)


def _country_text(value: object) -> str:
    normalized = _text(value)
    aliases = {
        "uk": "great britain",
        "united kingdom": "great britain",
        "usa": "united states",
        "us": "united states",
        "uae": "united arab emirates",
    }
    return aliases.get(normalized, normalized)


def _similarity(left: object, right: object, *, names: bool = False) -> float:
    lhs = _name_text(left) if names else _text(left)
    rhs = _name_text(right) if names else _text(right)
    if not lhs or not rhs:
        return 0
    if lhs == rhs:
        return 1
    lhs_tokens = set(lhs.split())
    rhs_tokens = set(rhs.split())
    token_score = len(lhs_tokens & rhs_tokens) / max(1, len(lhs_tokens | rhs_tokens))
    sequence_score = SequenceMatcher(None, lhs, rhs).ratio()
    return max(token_score, sequence_score * 0.9)


def _date(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class OpenF1SessionMatcher:
    """Confidence-scored Jolpica meeting to OpenF1 session matching.

    A match requires compatible season/date metadata and corroborating event,
    country, or circuit information.  A close tie is deliberately unresolved
    so telemetry can never be silently assigned to the wrong weekend.
    """

    minimum_meeting_score = 0.55
    ambiguity_margin = 0.08

    def match_meeting(
        self,
        meeting: RaceMeeting,
        sessions: list[dict[str, Any]],
    ) -> ProviderMeetingMatch:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for index, session in enumerate(sessions):
            if str(session.get("year") or meeting.season_year) != str(meeting.season_year):
                continue
            meeting_key = session.get("meeting_key")
            start = _date(session.get("date_start"))
            week_start = start.date() - timedelta(days=start.weekday()) if start else "unknown"
            fallback = "|".join(
                (
                    _name_text(session.get("meeting_name")),
                    _country_text(session.get("country_name")),
                    _text(session.get("circuit_short_name")),
                    str(week_start),
                )
            )
            derived_key = f"derived:{fallback}" if fallback.strip("|") else f"derived:{index}"
            groups[str(meeting_key) if meeting_key is not None else derived_key].append(session)

        ranked: list[tuple[float, str, list[dict[str, Any]], dict[str, float]]] = []
        for meeting_key, candidates in groups.items():
            scored = self._score_meeting(meeting, candidates)
            if scored is not None:
                score, evidence = scored
                ranked.append((score, meeting_key, candidates, evidence))
        ranked.sort(key=lambda item: item[0], reverse=True)
        if not ranked or ranked[0][0] < self.minimum_meeting_score:
            return ProviderMeetingMatch(
                confidence=MatchConfidence.UNRESOLVED,
                reason="No OpenF1 meeting had enough corroborating metadata",
            )
        best = ranked[0]
        if len(ranked) > 1 and best[0] - ranked[1][0] < self.ambiguity_margin:
            logger.warning(
                "Ambiguous OpenF1 meeting match season=%s round=%s candidates=%s",
                meeting.season_year,
                meeting.round_number,
                [item[1] for item in ranked[:2]],
            )
            return ProviderMeetingMatch(
                confidence=MatchConfidence.AMBIGUOUS,
                score=best[0],
                reason="Two OpenF1 meetings matched with similar confidence",
            )
        confidence = MatchConfidence.HIGH if best[0] >= 0.8 else MatchConfidence.MEDIUM
        return ProviderMeetingMatch(
            meeting_key=best[1],
            confidence=confidence,
            score=round(best[0], 4),
            reason=self._reason(best[3]),
            sessions=best[2],
        )

    def match_session(
        self,
        meeting: RaceMeeting,
        sessions: list[dict[str, Any]],
        session_type: CompetitiveSessionType | str,
        *,
        scheduled_start: datetime | None = None,
        meeting_key: str | None = None,
    ) -> ProviderSessionMatch:
        expected_type = normalize_session_type(session_type)
        if expected_type is None:
            raise ValueError(f"Unsupported competitive session type: {session_type}")
        if meeting_key is None:
            meeting_match = self.match_meeting(meeting, sessions)
            if not meeting_match.resolved:
                return ProviderSessionMatch(
                    session_type=expected_type,
                    confidence=meeting_match.confidence,
                    score=meeting_match.score,
                    reason=meeting_match.reason,
                )
            meeting_key = meeting_match.meeting_key
            candidates = meeting_match.sessions
        else:
            candidates = [
                session
                for session in sessions
                if str(session.get("meeting_key")) == str(meeting_key)
            ]
        typed = [
            session
            for session in candidates
            if normalize_session_type(session.get("session_name") or session.get("session_type"))
            == expected_type
        ]
        if not typed:
            return ProviderSessionMatch(
                meeting_key=meeting_key,
                session_type=expected_type,
                confidence=MatchConfidence.UNRESOLVED,
                reason="The matched meeting does not expose this session type",
            )

        expected_start = scheduled_start or self._scheduled_start(meeting, expected_type)
        ranked: list[tuple[float, dict[str, Any]]] = []
        for session in typed:
            start = _date(session.get("date_start"))
            if expected_start is None or start is None:
                score = 0.75
            else:
                distance_hours = abs((start - expected_start).total_seconds()) / 3600
                if distance_hours > 12:
                    continue
                score = 1 - min(distance_hours, 12) / 48
            ranked.append((score, session))
        ranked.sort(key=lambda item: item[0], reverse=True)
        if not ranked:
            return ProviderSessionMatch(
                meeting_key=meeting_key,
                session_type=expected_type,
                confidence=MatchConfidence.UNRESOLVED,
                reason="Session time is not compatible with the official schedule",
            )
        if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.03:
            logger.warning(
                "Ambiguous OpenF1 session match meeting=%s type=%s",
                meeting_key,
                expected_type.value,
            )
            return ProviderSessionMatch(
                meeting_key=meeting_key,
                session_type=expected_type,
                confidence=MatchConfidence.AMBIGUOUS,
                score=ranked[0][0],
                reason="Multiple provider sessions have the same schedule confidence",
            )
        best_score, best_session = ranked[0]
        session_key = best_session.get("session_key")
        if session_key is None:
            return ProviderSessionMatch(
                meeting_key=meeting_key,
                session_type=expected_type,
                confidence=MatchConfidence.UNRESOLVED,
                score=best_score,
                reason="The provider session has no session_key",
            )
        return ProviderSessionMatch(
            session_key=str(session_key),
            meeting_key=meeting_key,
            session_type=expected_type,
            confidence=MatchConfidence.HIGH if best_score >= 0.9 else MatchConfidence.MEDIUM,
            score=round(best_score, 4),
            reason="Session type and official start time match",
            session=best_session,
        )

    @staticmethod
    def _score_meeting(
        meeting: RaceMeeting,
        sessions: list[dict[str, Any]],
    ) -> tuple[float, dict[str, float]] | None:
        starts = [start for row in sessions if (start := _date(row.get("date_start")))]
        if not starts:
            return None
        race_candidates = [
            start
            for row in sessions
            if normalize_session_type(row.get("session_name")) == CompetitiveSessionType.RACE
            and (start := _date(row.get("date_start")))
        ]
        closest = min(
            race_candidates or starts,
            key=lambda start: abs((start.date() - meeting.race_date).days),
        )
        day_distance = abs((closest.date() - meeting.race_date).days)
        if day_distance > 3:
            return None
        date_score = max(0.0, 1 - day_distance / 4)
        country_score = max(
            _similarity(_country_text(meeting.country), _country_text(row.get("country_name")))
            for row in sessions
        )
        circuit_score = max(
            _similarity(meeting.circuit_name, row.get("circuit_short_name")) for row in sessions
        )
        name_score = max(
            _similarity(meeting.race_name, row.get("meeting_name"), names=True) for row in sessions
        )
        evidence = {
            "date": date_score,
            "country": country_score,
            "circuit": circuit_score,
            "name": name_score,
        }
        # Date is mandatory; at least one identity signal must corroborate it.
        if max(country_score, circuit_score, name_score) < 0.35:
            return None
        return (
            date_score * 0.35 + country_score * 0.20 + circuit_score * 0.25 + name_score * 0.20,
            evidence,
        )

    @staticmethod
    def _scheduled_start(
        meeting: RaceMeeting, session_type: CompetitiveSessionType
    ) -> datetime | None:
        for session in meeting.sessions:
            if normalize_session_type(session.name) == session_type:
                return session.starts_at
        return meeting.race_start if session_type == CompetitiveSessionType.RACE else None

    @staticmethod
    def _reason(evidence: dict[str, float]) -> str:
        confirmed = [name for name, score in evidence.items() if score >= 0.65]
        return "Matched by " + ", ".join(confirmed or ["combined provider metadata"])
