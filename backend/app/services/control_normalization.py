# SPDX-License-Identifier: AGPL-3.0-only
"""Explicit supported provider meanings; mentions and unmapped codes stay unknown."""

from __future__ import annotations

from app.domain.control import ControlSemantics


def decode_openf1_drs(value: object) -> str:
    # OpenF1 docs describe 8 as eligibility, not observed wing opening.
    # https://openf1.org/docs/#car-data (checked 2026-09-12).
    if isinstance(value, bool) or not isinstance(value, int):
        return "unknown"
    if value in {0, 1}:
        return "closed"
    return "open" if value in {10, 12, 14} else "unknown"


def normalize_control_payload(payload: dict[str, object]) -> dict[str, object]:
    message = " ".join(str(payload.get("message") or "").upper().split())
    scope = str(payload.get("scope") or "").upper()
    category = str(payload.get("category") or "").upper()
    flag = str(payload.get("flag") or "").upper()
    result = ControlSemantics()
    # A disciplinary message mentioning a condition is not a transition.
    if any(word in message for word in ("PENALTY", "INVESTIGATION", "NOTED", "INFRINGEMENT")):
        return result.model_dump(exclude_none=True)
    if scope == "SECTOR":
        sector = payload.get("sector")
        if isinstance(sector, int) and not isinstance(sector, bool) and 1 <= sector <= 100:
            result.sector = sector
            result.flag = {
                "GREEN": "green",
                "CLEAR": "green",
                "YELLOW": "yellow",
                "DOUBLE YELLOW": "double_yellow",
                "RED": "red",
            }.get(flag)
        return result.model_dump(exclude_none=True)
    if scope not in {"", "TRACK", "SESSION"}:
        return result.model_dump(exclude_none=True)
    transitions = {
        "SAFETY CAR DEPLOYED": "safety_car",
        "SAFETY CAR IN THIS LAP": "safety_car_ending",
        "SAFETY CAR ENDING": "safety_car_ending",
        "SAFETY CAR ENDED": "green",
        "VIRTUAL SAFETY CAR DEPLOYED": "virtual_safety_car",
        "VSC DEPLOYED": "virtual_safety_car",
        "VIRTUAL SAFETY CAR ENDING": "virtual_safety_car_ending",
        "VSC ENDING": "virtual_safety_car_ending",
        "VIRTUAL SAFETY CAR ENDED": "green",
        "VSC ENDED": "green",
    }
    if message in transitions:
        result.neutralization = transitions[message]
        if result.neutralization == "green":
            result.flag = "green"
    if message in {"DRS ENABLED", "DRS DISABLED"}:
        result.drs_permission = "enabled" if message == "DRS ENABLED" else "disabled"
    if flag in {"GREEN", "CLEAR", "YELLOW", "DOUBLE YELLOW", "RED"}:
        result.flag = {"CLEAR": "green"}.get(flag, flag.lower().replace(" ", "_"))
    if flag == "RED" or message == "RED FLAG":
        result.lifecycle = "suspended"
        result.neutralization = "red"
    if flag == "GREEN":
        result.neutralization = "green"
    if category == "SESSIONSTATUS":
        status = message.removeprefix("SESSION ")
        result.lifecycle = {
            "STARTED": "running",
            "RESUMED": "running",
            "SUSPENDED": "suspended",
            "DELAYED": "delayed",
            "FINISHED": "finished",
            "ENDED": "finished",
            "CHEQUERED": "finished",
        }.get(status, result.lifecycle)
        if status == "SUSPENDED":
            result.neutralization = "red"
    if flag == "CHEQUERED" and scope in {"TRACK", "SESSION"}:
        result.lifecycle = "finished"
    return result.model_dump(exclude_none=True)
