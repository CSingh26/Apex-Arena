# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import datetime

from pydantic import ValidationError

from app.domain.control import (
    ControlObservation,
    ControlProjection,
    ControlSemantics,
    ControlTransition,
    CurrentControlProjection,
)
from app.domain.models import EventOrigin, NormalizedRaceEvent, RaceEventType
from app.domain.strategy import FactReference


def racing_inference_blocked(control: CurrentControlProjection) -> bool:
    """Known non-racing control blocks inference; unknown is not proof of green."""
    return control.lifecycle.value in {
        "scheduled",
        "delayed",
        "suspended",
        "finished",
    } or control.neutralization.value in {
        "safety_car",
        "safety_car_ending",
        "virtual_safety_car",
        "virtual_safety_car_ending",
        "red",
    }


def _observe(
    current: ControlObservation, value: str | None, evidence: FactReference
) -> ControlObservation:
    if value is None:
        return current
    if current.evidence is not None and (evidence.observed_at, evidence.sequence) < (
        current.evidence.observed_at,
        current.evidence.sequence,
    ):
        return current
    return ControlObservation(value=value, evidence=evidence)


def _transition_evidence(row: ControlTransition) -> FactReference:
    return FactReference(
        event_id=row.event_id,
        sequence=row.sequence,
        observed_at=row.observed_at,
        source=row.source,
    )


def _rebuild_current_from_history(result: ControlProjection) -> None:
    """Project current fields in effective-time order after late source arrival."""

    lifecycle = ControlObservation()
    neutralization = ControlObservation()
    drs_permission = ControlObservation()
    track_flag = ControlObservation()
    sector_flags: dict[str, ControlObservation] = {}
    for row in sorted(result.history, key=lambda item: (item.observed_at, item.sequence)):
        semantics = row.semantics
        evidence = _transition_evidence(row)
        if semantics.lifecycle == "running" and lifecycle.value != "finished":
            lifecycle = ControlObservation(value="running", evidence=evidence)
            if neutralization.value == "red":
                neutralization = ControlObservation(value="unknown", evidence=evidence)
            if track_flag.value == "red":
                track_flag = ControlObservation(value="unknown", evidence=evidence)
        elif semantics.lifecycle is not None and lifecycle.value != "finished":
            lifecycle = ControlObservation(value=semantics.lifecycle, evidence=evidence)
        neutralization = _observe(neutralization, semantics.neutralization, evidence)
        drs_permission = _observe(drs_permission, semantics.drs_permission, evidence)
        if semantics.sector is None:
            track_flag = _observe(track_flag, semantics.flag, evidence)
        elif semantics.flag is not None:
            key = str(semantics.sector)
            sector_flags[key] = _observe(
                sector_flags.get(key, ControlObservation()), semantics.flag, evidence
            )
    result.lifecycle = lifecycle
    result.neutralization = neutralization
    result.drs_permission = drs_permission
    result.track_flag = track_flag
    result.sector_flags = sector_flags


def _invalidate_red_before_retained_running(result: ControlProjection) -> None:
    """Apply a retained effective-time resume after a late red arrival."""

    running_evidence = [
        _transition_evidence(row) for row in result.history if row.semantics.lifecycle == "running"
    ]
    for field in ("neutralization", "track_flag"):
        current = getattr(result, field)
        if current.value != "red" or current.evidence is None:
            continue
        current_key = (current.evidence.observed_at, current.evidence.sequence)
        later_running = [
            evidence
            for evidence in running_evidence
            if (evidence.observed_at, evidence.sequence) > current_key
        ]
        if later_running:
            setattr(
                result,
                field,
                ControlObservation(
                    value="unknown",
                    evidence=min(
                        later_running,
                        key=lambda evidence: (evidence.observed_at, evidence.sequence),
                    ),
                ),
            )


def apply_control(state: ControlProjection, event: NormalizedRaceEvent) -> ControlProjection:
    if event.session_key != state.session_key:
        raise ValueError("Control session mismatch")
    result = state.model_copy(deep=True)
    if event.sequence_number <= result.sequence or event.event_origin != EventOrigin.SOURCE_FACT:
        return result
    result.sequence = event.sequence_number
    raw = event.payload.get("control")
    terminal = event.event_type in {RaceEventType.SESSION_FINISH, RaceEventType.SESSION_END}
    try:
        semantics = ControlSemantics.model_validate(raw if isinstance(raw, dict) else {})
    except ValidationError:
        if not terminal:
            return result
        semantics = ControlSemantics()
    if terminal:
        semantics.lifecycle = "finished"
    evidence = FactReference(
        event_id=event.id,
        sequence=event.sequence_number,
        observed_at=event.event_time,
        source=event.source,
    )
    if semantics.model_dump(exclude_none=True):
        result.history.append(ControlTransition(**evidence.model_dump(), semantics=semantics))
        result.history_truncated |= len(result.history) > 120
        result.history = result.history[-120:]
    if not result.history_truncated:
        _rebuild_current_from_history(result)
    else:
        # The dropped prefix can contain still-current orthogonal fields. Keep
        # timestamp-guarded current observations rather than fabricating a new
        # baseline from the bounded suffix.
        current_semantics = semantics.model_copy(deep=True)
        if current_semantics.lifecycle == "running" and result.lifecycle.value != "finished":
            if result.neutralization.value == "red":
                current_semantics.neutralization = "unknown"
            if result.track_flag.value == "red":
                current_semantics.flag = "unknown"
        if current_semantics.lifecycle == "finished":
            # Whole-session finish is absorbing even when it arrives late and
            # the bounded transition suffix has evicted older observations.
            # Preserve the same earliest effective finish as full-history replay.
            prior = result.lifecycle.evidence
            if (
                result.lifecycle.value != "finished"
                or prior is None
                or (evidence.observed_at, evidence.sequence) < (prior.observed_at, prior.sequence)
            ):
                result.lifecycle = ControlObservation(value="finished", evidence=evidence)
        elif result.lifecycle.value != "finished":
            result.lifecycle = _observe(result.lifecycle, current_semantics.lifecycle, evidence)
        result.neutralization = _observe(
            result.neutralization, current_semantics.neutralization, evidence
        )
        result.drs_permission = _observe(
            result.drs_permission, current_semantics.drs_permission, evidence
        )
        if current_semantics.sector is None:
            result.track_flag = _observe(result.track_flag, current_semantics.flag, evidence)
        elif current_semantics.flag is not None:
            key = str(current_semantics.sector)
            result.sector_flags[key] = _observe(
                result.sector_flags.get(key, ControlObservation()),
                current_semantics.flag,
                evidence,
            )
        _invalidate_red_before_retained_running(result)
    return result


def lap_control_context(state: ControlProjection, starts_at: datetime, ends_at: datetime) -> str:
    """Classify the whole observed lap interval, using only this consumed cursor.

    A truncated control prefix cannot prove absence of an uncleared local flag.
    Unknown is deliberately preferable to inventing a clean racing sample.
    """
    if ends_at < starts_at or state.history_truncated:
        return "unknown"
    transitions = sorted(
        (row for row in state.history if row.observed_at <= ends_at),
        key=lambda row: (row.observed_at, row.sequence),
    )
    neutralization = "unknown"
    flags: dict[str, str] = {}
    initial_known = False
    unsafe = False
    for row in transitions:
        semantics = row.semantics
        if row.observed_at > starts_at and not initial_known:
            initial_known = neutralization != "unknown"
            if not initial_known:
                return "unknown"
            unsafe |= neutralization != "green" or any(
                value in {"yellow", "double_yellow", "red"} for value in flags.values()
            )
        if semantics.lifecycle == "running":
            if neutralization == "red":
                neutralization = "unknown"
            if flags.get("track") == "red":
                flags["track"] = "unknown"
        if semantics.neutralization is not None:
            neutralization = semantics.neutralization
        if semantics.flag is not None:
            flags[str(semantics.sector) if semantics.sector else "track"] = semantics.flag
        if row.observed_at >= starts_at:
            unsafe |= neutralization not in {"green", "unknown"} or any(
                value in {"yellow", "double_yellow", "red"} for value in flags.values()
            )
    if unsafe:
        return "neutralized"
    if neutralization == "unknown" or any(value == "unknown" for value in flags.values()):
        return "unknown"
    if neutralization != "green" or any(
        value in {"yellow", "double_yellow", "red"} for value in flags.values()
    ):
        return "neutralized"
    return "green"
