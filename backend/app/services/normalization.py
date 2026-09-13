# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.domain.models import NormalizedRaceEvent, RaceEventType
from app.services.control_normalization import normalize_control_payload
from app.services.event_importance import EventImportancePolicy
from app.services.raw_events import RawEventInput
from app.services.session_semantics import (
    normalize_qualifying_phase,
    normalize_session_type,
    phase_result_rows,
)

ENDPOINT_EVENT_TYPES = {
    "sessions": RaceEventType.SESSION_START,
    "drivers": RaceEventType.DRIVER_UPDATE,
    "position": RaceEventType.POSITION_SAMPLE,
    "intervals": RaceEventType.INTERVAL_SAMPLE,
    "laps": RaceEventType.LAP_COMPLETED,
    "pit": RaceEventType.PIT_STOP,
    "stints": RaceEventType.STINT_UPDATE,
    "weather": RaceEventType.WEATHER_UPDATE,
    "car_data": RaceEventType.CAR_DATA_SAMPLE,
    "location": RaceEventType.LOCATION_SAMPLE,
    "session_result": RaceEventType.SESSION_RESULT,
    "starting_grid": RaceEventType.STARTING_GRID,
}


class OpenF1EventNormalizer:
    """Map live and historical OpenF1 records into one low-level event contract."""

    def __init__(self, *, importance_policy: EventImportancePolicy | None = None) -> None:
        self.importance_policy = importance_policy or EventImportancePolicy()

    def normalize(self, raw: RawEventInput, raw_event_id: UUID) -> NormalizedRaceEvent:
        endpoint = raw.provider_endpoint.removeprefix("v1/").strip("/")
        payload = self._enrich_payload(endpoint, raw.raw_payload)
        event_type = self._event_type(endpoint, payload)
        event_time = raw.event_time or self._payload_time(payload) or raw.received_at
        identity_time = event_time
        if endpoint == "sessions":
            observed = self._payload_time({key: payload.get(key) for key in ("date", "event_time")})
            if payload.get("status"):
                event_time = observed or raw.received_at
                payload["timestamp_authority"] = (
                    "provider_observation" if observed else "received_observation"
                )
            else:
                payload["timestamp_authority"] = "scheduled_metadata"
            # A status without a provider timestamp is an observation, not a new
            # fact on every poll. Its raw content supplies stable source identity.
            identity_time = observed
        if event_time.tzinfo is None:
            event_time = event_time.replace(tzinfo=UTC)
        session_key = str(raw.session_key or payload.get("session_key") or "unknown")
        driver_numbers = self._driver_numbers(payload)
        lap_number = self._optional_int(payload.get("lap_number"))
        source = f"{raw.provider}_historical" if raw.is_replay else raw.provider
        dedup_key = self._dedup_key(
            session_key=session_key,
            event_type=event_type,
            event_time=identity_time,
            driver_numbers=driver_numbers,
            lap_number=lap_number,
            payload=payload,
        )
        event = NormalizedRaceEvent(
            session_id=raw.session_id,
            session_key=session_key,
            source=source,
            raw_event_id=raw_event_id,
            event_time=event_time,
            received_at=raw.received_at,
            event_type=event_type,
            driver_numbers=driver_numbers,
            primary_driver_number=driver_numbers[0] if driver_numbers else None,
            lap_number=lap_number,
            payload=payload,
            dedup_key=dedup_key,
            is_replay=raw.is_replay,
        )
        importance_level, importance, _ = self.importance_policy.classify(event)
        return event.model_copy(
            update={
                "importance_level": importance_level,
                "importance": importance,
            }
        )

    def _event_type(self, endpoint: str, payload: dict[str, Any]) -> RaceEventType:
        if endpoint == "race_control":
            return self._race_control_type(payload)
        if endpoint == "sessions" and payload.get("status"):
            return RaceEventType.SESSION_STATUS
        return ENDPOINT_EVENT_TYPES.get(endpoint, RaceEventType.UNKNOWN_PROVIDER_EVENT)

    @staticmethod
    def _race_control_type(payload: dict[str, Any]) -> RaceEventType:
        message = str(payload.get("message") or "").upper()
        flag = str(payload.get("flag") or "").upper()
        category = str(payload.get("category") or "").upper()
        if "LAP TIME" in message and ("DELETED" in message or "INVALIDATED" in message):
            return RaceEventType.LAP_DELETED
        if "PENALTY" in message:
            return RaceEventType.PENALTY
        if "INVESTIGATION" in message:
            return RaceEventType.INVESTIGATION
        if re.search(r"\b(?:SQ|Q)[123]\b", message) and category == "SESSIONSTATUS":
            return RaceEventType.QUALIFYING_PHASE
        control = payload.get("control") or {}
        if control.get("lifecycle") == "finished":
            return RaceEventType.SESSION_FINISH
        if control.get("neutralization") == "virtual_safety_car":
            return RaceEventType.VIRTUAL_SAFETY_CAR
        if control.get("neutralization") == "safety_car":
            return RaceEventType.SAFETY_CAR
        if control.get("neutralization") == "red":
            return RaceEventType.RED_FLAG
        if "YELLOW" in flag or "YELLOW FLAG" in message:
            return RaceEventType.YELLOW_FLAG
        if payload.get("qualifying_phase") is not None and (
            category == "SESSIONSTATUS" or re.search(r"\b(?:SQ|Q)[123]\b.*\b(?:START|END)", message)
        ):
            return RaceEventType.QUALIFYING_PHASE
        return RaceEventType.RACE_CONTROL

    @staticmethod
    def _enrich_payload(endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(payload)
        if endpoint == "race_control":
            enriched["control"] = normalize_control_payload(payload)
            phase_match = re.search(r"\b(?:SQ|Q)[123]\b", str(payload.get("message") or "").upper())
            if phase_match and str(payload.get("category") or "").upper() == "SESSIONSTATUS":
                enriched["session_phase"] = phase_match.group()
                # A phase chequered flag cannot certify whole-session completion.
                if enriched["control"].get("lifecycle") == "finished":
                    enriched["control"].pop("lifecycle")
            enriched["control_schema"] = "observed-v1"
            enriched["timestamp_authority"] = (
                "provider_observation"
                if OpenF1EventNormalizer._payload_time(payload)
                else "received_observation"
            )
        elif endpoint == "sessions":
            enriched["control_schema"] = "observed-v1"
            enriched["control"] = normalize_control_payload(
                {"category": "SessionStatus", "message": str(payload.get("status") or "")}
            )
        session_type = normalize_session_type(
            enriched.get("normalized_session_type")
            or enriched.get("session_name")
            or enriched.get("session_type")
        )
        if session_type is not None:
            enriched["normalized_session_type"] = session_type.value
        phase = normalize_qualifying_phase(
            enriched.get("session_phase") or enriched.get("qualifying_phase"),
            session_type,
        )
        if endpoint == "laps" and phase is not None:
            supplied = (
                str(enriched.get("session_phase") or enriched.get("qualifying_phase") or "")
                .strip()
                .upper()
            )
            explicit_family = re.fullmatch(r"(SQ|Q)\s*[123]", supplied)
            if explicit_family and explicit_family.group(1) != phase[:-1]:
                phase = None
        if phase is not None:
            enriched["session_phase"] = phase
        if endpoint == "session_result" and session_type is not None:
            rows = phase_result_rows(enriched, session_type)
            if rows:
                enriched["phase_results"] = rows
        return enriched

    @classmethod
    def _payload_time(cls, payload: dict[str, Any]) -> datetime | None:
        for field in ("date", "date_start", "event_time"):
            value = payload.get(field)
            if not isinstance(value, str) or not value:
                continue
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        return None

    @classmethod
    def _driver_numbers(cls, payload: dict[str, Any]) -> list[int]:
        candidates = (
            payload.get("driver_number"),
            payload.get("overtaking_driver_number"),
            payload.get("overtaken_driver_number"),
        )
        numbers = [number for value in candidates if (number := cls._optional_int(value))]
        return list(dict.fromkeys(numbers))

    @staticmethod
    def _optional_int(value: object) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _dedup_key(
        *,
        session_key: str,
        event_type: RaceEventType,
        event_time: datetime | None,
        driver_numbers: list[int],
        lap_number: int | None,
        payload: dict[str, Any],
    ) -> str:
        identity = {
            "session_key": session_key,
            "event_type": event_type.value,
            "event_time": event_time.isoformat() if event_time else None,
            "driver_numbers": driver_numbers,
            "lap_number": lap_number,
            "payload": payload,
        }
        canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()
