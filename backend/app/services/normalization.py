# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.domain.models import NormalizedRaceEvent, RaceEventType
from app.services.raw_events import RawEventInput

ENDPOINT_EVENT_TYPES = {
    "sessions": RaceEventType.SESSION_START,
    "drivers": RaceEventType.DRIVER_UPDATE,
    "position": RaceEventType.POSITION_SAMPLE,
    "intervals": RaceEventType.INTERVAL_SAMPLE,
    "laps": RaceEventType.LAP_COMPLETED,
    "pit": RaceEventType.PIT_STOP,
    "stints": RaceEventType.STINT_UPDATE,
    "weather": RaceEventType.WEATHER_UPDATE,
}


class OpenF1EventNormalizer:
    """Map live and historical OpenF1 records into one low-level event contract."""

    def normalize(self, raw: RawEventInput, raw_event_id: UUID) -> NormalizedRaceEvent:
        endpoint = raw.provider_endpoint.removeprefix("v1/").strip("/")
        payload = raw.raw_payload
        event_type = self._event_type(endpoint, payload)
        event_time = raw.event_time or self._payload_time(payload) or raw.received_at
        if event_time.tzinfo is None:
            event_time = event_time.replace(tzinfo=UTC)
        session_key = str(raw.session_key or payload.get("session_key") or "unknown")
        driver_numbers = self._driver_numbers(payload)
        lap_number = self._optional_int(payload.get("lap_number"))
        source = f"{raw.provider}_historical" if raw.is_replay else raw.provider
        dedup_key = self._dedup_key(
            session_key=session_key,
            event_type=event_type,
            event_time=event_time,
            driver_numbers=driver_numbers,
            lap_number=lap_number,
            payload=payload,
        )
        return NormalizedRaceEvent(
            session_id=raw.session_id,
            session_key=session_key,
            source=source,
            raw_event_id=raw_event_id,
            event_time=event_time,
            received_at=raw.received_at,
            event_type=event_type,
            driver_numbers=driver_numbers,
            lap_number=lap_number,
            payload=payload,
            dedup_key=dedup_key,
            is_replay=raw.is_replay,
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
        if "VIRTUAL SAFETY CAR" in message or "VSC" in message:
            return RaceEventType.VIRTUAL_SAFETY_CAR
        if "SAFETY CAR" in message:
            return RaceEventType.SAFETY_CAR
        if flag == "RED" or "RED FLAG" in message:
            return RaceEventType.RED_FLAG
        if "YELLOW" in flag or "YELLOW FLAG" in message:
            return RaceEventType.YELLOW_FLAG
        if "PENALTY" in message:
            return RaceEventType.PENALTY
        if "INVESTIGATION" in message:
            return RaceEventType.INVESTIGATION
        if category == "SESSIONSTATUS" and any(
            marker in message for marker in ("FINISH", "CHEQUERED", "ENDED")
        ):
            return RaceEventType.SESSION_FINISH
        return RaceEventType.RACE_CONTROL

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
        event_time: datetime,
        driver_numbers: list[int],
        lap_number: int | None,
        payload: dict[str, Any],
    ) -> str:
        identity = {
            "session_key": session_key,
            "event_type": event_type.value,
            "event_time": event_time.isoformat(),
            "driver_numbers": driver_numbers,
            "lap_number": lap_number,
            "payload": payload,
        }
        canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()
