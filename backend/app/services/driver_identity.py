# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class DriverIdentity(BaseModel):
    driver_number: int
    full_name: str
    broadcast_name: str | None = None
    team_name: str | None = None
    name_acronym: str | None = None

    @property
    def public_name(self) -> str:
        return self.broadcast_name or self.full_name


class DriverIdentityResolver:
    """Resolve provider driver numbers without guessing identities or teams."""

    def __init__(self) -> None:
        self._logged_unresolved: set[int] = set()

    def build_registry(self, rows: Iterable[dict[str, Any]]) -> dict[int, DriverIdentity]:
        registry: dict[int, DriverIdentity] = {}
        for row in rows:
            identity = self.from_payload(row)
            if identity is not None:
                registry[identity.driver_number] = identity
        return registry

    def from_payload(self, payload: dict[str, Any]) -> DriverIdentity | None:
        number = self._number(payload.get("driver_number"))
        if number is None:
            return None
        full_name = self._clean(payload.get("full_name"))
        if not full_name:
            first_name = self._clean(payload.get("first_name") or payload.get("given_name"))
            last_name = self._clean(payload.get("last_name") or payload.get("family_name"))
            full_name = " ".join(value for value in (first_name, last_name) if value)
        broadcast = self._clean(payload.get("broadcast_name"))
        acronym = self._clean(payload.get("name_acronym"))
        if not full_name:
            full_name = broadcast or acronym or ""
        if not full_name:
            logger.warning("Unresolved OpenF1 driver identity number=%s", number)
            return None
        return DriverIdentity(
            driver_number=number,
            full_name=full_name,
            broadcast_name=broadcast,
            team_name=self._clean(payload.get("team_name")) or None,
            name_acronym=acronym or None,
        )

    def enrich(
        self,
        payload: dict[str, Any],
        registry: dict[int, DriverIdentity],
    ) -> dict[str, Any]:
        number = self._number(payload.get("driver_number"))
        if number is None:
            return dict(payload)
        identity = registry.get(number)
        if identity is None:
            if number not in self._logged_unresolved:
                logger.warning("No verified driver metadata for car number=%s", number)
                self._logged_unresolved.add(number)
            return dict(payload)
        enriched = dict(payload)
        enriched["resolved_driver_name"] = identity.full_name
        if identity.broadcast_name:
            enriched["resolved_broadcast_name"] = identity.broadcast_name
        if identity.team_name:
            enriched["resolved_team_name"] = identity.team_name
        return enriched

    @classmethod
    def public_label(cls, payload: dict[str, Any], driver_number: int | None) -> str:
        for field in (
            "resolved_driver_name",
            "resolved_broadcast_name",
            "full_name",
            "broadcast_name",
        ):
            value = cls._clean(payload.get(field))
            if value:
                return value
        relevant_state = payload.get("relevant_driver_state")
        if driver_number is not None and isinstance(relevant_state, dict):
            driver_state = relevant_state.get(str(driver_number))
            if isinstance(driver_state, dict):
                for field in ("full_name", "broadcast_name"):
                    value = cls._clean(driver_state.get(field))
                    if value:
                        return value
        if driver_number is not None:
            return f"The driver in car {driver_number}"
        return "The driver"

    @staticmethod
    def _number(value: object) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _clean(value: object) -> str:
        return " ".join(str(value or "").split())
