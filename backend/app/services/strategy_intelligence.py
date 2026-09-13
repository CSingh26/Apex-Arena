# SPDX-License-Identifier: AGPL-3.0-only
"""Deterministic source-order strategy frames and transition admission. No I/O."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from uuid import NAMESPACE_URL, uuid5

from pydantic import ValidationError

from app.domain.models import RaceEventType as E
from app.domain.strategy_situations import (
    Capability,
    SituationPayload,
    StrategyDelta,
    StrategyFrame,
    StrategySituation,
    StrategyTransition,
    encoded,
)
from app.services.intelligence_context import HISTORY_TYPES
from app.services.strategy_battles import contexts
from app.services.strategy_inputs import SourceIndex, driver_products, evidence, relative
from app.services.strategy_pit_context import capture_cycle, cycle_candidates, pit_windows


@dataclass
class Emission:
    anchor: object
    last_time: object = None
    signature: str | None = None
    emitted: StrategySituation | None = None


@dataclass
class Session:
    index: SourceIndex = field(default_factory=SourceIndex)
    products: dict = field(default_factory=dict)
    emissions: dict = field(default_factory=dict)
    active: set = field(default_factory=set)
    sequence: int = 0
    cycles: dict = field(default_factory=dict)
    trends: dict = field(default_factory=dict)


DEFAULT_SEMANTIC_IDENTITY = "strategy-v1"


class StrategyIntelligence:
    def __init__(self, *, semantic_identity: str = DEFAULT_SEMANTIC_IDENTITY):
        self.semantic_identity = semantic_identity
        self.sessions = {}

    def reset_session(self, session):
        self.sessions.pop(session, None)

    def advance(self, source, state, context):
        session = self.sessions.setdefault(source.session_key, Session())
        now = context.analysis_time
        frame = StrategyFrame(
            session_key=source.session_key,
            sequence=source.sequence_number,
            history_sequence=context.relevant_sequence,
            history_event_id=context.relevant_event_id,
            analysis_time=now,
            semantic_identity=self.semantic_identity,
        )
        if source.sequence_number <= session.sequence:
            return StrategyDelta(frame=frame)
        session.sequence = source.sequence_number
        session.index.advance(source)
        # Capture the actual pit against the preceding measured pace, before replacing products.
        capture_cycle(session, source, context, now, session.index.pairs(set(session.products)))
        relevant = source.event_type in HISTORY_TYPES or bool(source.payload.get("control"))
        if relevant:
            numbers = (
                set(context.history.drivers)
                if source.payload.get("control")
                else {
                    str(
                        source.primary_driver_number
                        or (source.driver_numbers[0] if source.driver_numbers else 0)
                    )
                }
            )
            for key in sorted(numbers):
                if key in context.history.drivers and key in state.drivers:
                    session.products[int(key)] = driver_products(
                        int(key), context.history.drivers[key], context, state.drivers[key]
                    )
        active = {
            int(key)
            for key, value in state.drivers.items()
            if value.status not in {"RETIRED", "STOPPED", "DNF", "DNS"} and not value.in_pit
        }
        pairs = session.index.pairs(active)
        candidates = []
        neutral = context.control.neutralization.value
        running = (
            context.control.lifecycle.value not in {"suspended", "finished"} and neutral != "red"
        )
        racing = running and state.session_type in {"RACE", "SPRINT"}
        windows, timings = (
            pit_windows(session, context, now, active)
            if racing and neutral == "green"
            else ({}, {})
        )
        for driver, (window, refs, assumptions, limits) in windows.items():
            candidates.append(
                (
                    "pit_window",
                    (driver,),
                    SituationPayload(pit_window=window),
                    refs,
                    assumptions,
                    limits,
                )
            )
        for pair in pairs if racing else []:
            a, b = (session.products.get(d) for d in pair)
            if not a or not b:
                continue
            same, team_refs = session.index.team(pair)
            if (
                a.tyre.compound
                and b.tyre.compound
                and (
                    a.tyre.compound != b.tyre.compound
                    or a.tyre.stint_number != b.tyre.stint_number
                    or a.tyre.stop_count != b.tyre.stop_count
                )
            ):
                payload = SituationPayload(
                    tyres=[a.tyre, b.tyre],
                    same_reported_team=same,
                    age_offset_laps=a.tyre.age_laps - b.tyre.age_laps
                    if a.tyre.age_laps is not None and b.tyre.age_laps is not None
                    else None,
                )
                candidates.append(
                    (
                        "stint_divergence",
                        pair,
                        payload,
                        [*a.tyre_refs, *b.tyre_refs, *team_refs],
                        ["plan_unknown"],
                        [],
                    )
                )
            pace = relative(a, b, now) if neutral == "green" else None
            if pace and abs(pace.difference_seconds) >= 0.3:
                candidates.append(
                    (
                        "relative_pace",
                        pair,
                        SituationPayload(pace=pace),
                        [*a.pace_refs, *b.pace_refs],
                        ["observed_pace_not_causal_tyre_degradation"],
                        ["fuel_and_traffic_not_isolated", "weather_not_isolated"],
                    )
                )
            gap = session.index.gap(pair, now)
            if (
                pace
                and gap
                and 0 < gap[0] <= 3
                and pair[1] in windows
                and pair[0] in timings
                and pair[1] in timings
                and timings[pair[0]].lap_number == timings[pair[1]].lap_number
                and (a.tyre.compound != b.tyre.compound or pace.difference_seconds < 0)
            ):
                window, refs, assumptions, limits = windows[pair[1]]
                candidates.append(
                    (
                        "undercut_condition",
                        pair,
                        SituationPayload(
                            tyres=[a.tyre, b.tyre],
                            pace=pace,
                            pit_window=window,
                            gap_seconds=gap[0],
                            required_gain_seconds=gap[0],
                        ),
                        [*refs, *gap[1], *a.pace_refs, *b.pace_refs, *a.tyre_refs, *b.tyre_refs],
                        [*assumptions, "equal_stop_loss_cumulative_gain_must_exceed_gap"],
                        [
                            *limits,
                            "rival_stop_timing_unknown",
                            "new_tyre_pace_unknown",
                            "warmup_unknown",
                        ],
                    )
                )
        if racing:
            candidates.extend(cycle_candidates(session, context, now, active))
        if running and neutral in {
            "safety_car",
            "safety_car_ending",
            "virtual_safety_car",
            "virtual_safety_car_ending",
        }:
            ref = context.control.neutralization.evidence
            if ref:
                candidates.append(
                    (
                        "neutralized_pit_context",
                        (),
                        SituationPayload(neutralization=neutral),
                        [evidence(ref, source.session_key, "control", E.RACE_CONTROL.value)],
                        ["reduced_speed_pit_context"],
                        ["numeric_saving_unavailable"],
                    )
                )
        weather = state.weather_analysis
        if weather.availability == "available" and len(weather.evidence) == 2:
            changes = [
                weather.track_temperature_change_celsius,
                weather.air_temperature_change_celsius,
                weather.wind_speed_change_metres_per_second,
            ]
            wind_ok = len(context.history.weather) >= 2 and all(
                w.wind_speed is not None and w.wind_speed >= 1 for w in context.history.weather[-2:]
            )
            meaningful = (
                weather.rainfall_transition in {"rain_detected", "rain_no_longer_detected"}
                or any(x is not None and abs(x) >= 2 for x in changes)
                or (
                    wind_ok
                    and weather.wind_direction_change_degrees is not None
                    and abs(weather.wind_direction_change_degrees) >= 30
                )
            )
            if meaningful:
                previous = next(
                    (
                        w
                        for w in context.history.weather
                        if w.evidence.event_id == weather.evidence[0].event_id
                    ),
                    None,
                )
                payload = SituationPayload(
                    rainfall_before=previous.rainfall if previous else None,
                    rainfall_now=weather.current.rainfall,
                    track_temperature_change=changes[0],
                    air_temperature_change=changes[1],
                    wind_speed_change=changes[2],
                    wind_direction_change=weather.wind_direction_change_degrees
                    if wind_ok
                    else None,
                )
                candidates.append(
                    (
                        "weather_change",
                        (),
                        payload,
                        [
                            evidence(r, source.session_key, "weather", E.WEATHER_UPDATE.value)
                            for r in weather.evidence
                        ],
                        ["compound_choice_may_need_reassessment"],
                        weather.limitations,
                    )
                )
        transitions = []
        groups = []
        active_keys = set()
        for kind, pair, payload, refs, assumptions, limits in candidates:
            key = (kind, pair)
            active_keys.add(key)
            record = session.emissions.get(key)
            if record is None:
                if len(session.emissions) >= 2048:
                    frame.suppressed_events += 1
                    if "emission_state_budget" not in frame.limitations:
                        frame.limitations.append("emission_state_budget")
                    record = Emission(source.id)
                else:
                    record = session.emissions[key] = Emission(source.id)
            elif key not in session.active:
                record.anchor = source.id
            signature = self.signature(kind, payload)
            episode = uuid5(
                NAMESPACE_URL,
                f"{source.session_key}:{self.semantic_identity}:{key}:{record.anchor}",
            )
            revision = (
                uuid5(episode, f"{source.id}:{signature}")
                if signature != record.signature or record.emitted is None
                else record.emitted.revision_id
            )
            item = StrategySituation(
                situation_id=episode,
                revision_id=revision,
                kind=kind,
                participants=list(pair),
                source_anchor=record.anchor,
                source_sequence=source.sequence_number,
                session_key=source.session_key,
                sequence=source.sequence_number,
                history_sequence=context.relevant_sequence,
                analysis_time=now,
                semantic_identity=self.semantic_identity,
                payload=payload,
                evidence_keys=sorted({r.key for r in refs}),
                assumptions=assumptions,
                limitations=limits,
            )
            closure = {r.key: r for r in refs}
            groups.append((item, closure))
            frame.capabilities[kind] = Capability(
                availability="partial", reason="supported_observation"
            )
            if signature != record.signature and (
                record.last_time is None or (now - record.last_time).total_seconds() >= 60
            ):
                try:
                    item = item.model_copy(
                        update={
                            "transition": "opened" if record.emitted is None else "revised",
                            "superseded_revision_id": record.emitted.revision_id
                            if record.emitted
                            else None,
                        }
                    )
                    transition = StrategyTransition(situation=item, evidence=closure)
                except ValidationError:
                    frame.suppressed_events += 1
                else:
                    transitions.append(transition)
                    record.last_time, record.signature, record.emitted = now, signature, item
        # A deletion is an invalidating correction; elapsed freshness is not a retraction.
        if source.event_type == E.LAP_DELETED:
            for key in sorted(session.active - active_keys):
                record = session.emissions.get(key)
                if record is None or record.emitted is None or record.emitted.status == "withdrawn":
                    continue
                old = record.emitted
                item = old.model_copy(
                    update={
                        "status": "withdrawn",
                        "transition": "withdrawn",
                        "superseded_revision_id": old.revision_id,
                        "revision_id": uuid5(old.situation_id, f"withdrawn:{source.id}"),
                        "sequence": source.sequence_number,
                        "history_sequence": context.relevant_sequence,
                        "analysis_time": now,
                        "evidence_keys": [],
                        "payload": SituationPayload(),
                        "limitations": ["premise_invalidated"],
                    }
                )
                transitions.append(StrategyTransition(situation=item, evidence={}))
                record.emitted = item
                record.signature = None
                groups.insert(0, (item, {}))
        session.active = active_keys
        for key in list(session.emissions):
            record = session.emissions[key]
            if (
                key not in active_keys
                and record.last_time
                and (now - record.last_time).total_seconds() >= 60
            ):
                del session.emissions[key]
        # Atomic groups retain their entire evidence union and are revalidated at
        # the borrow boundary.
        for item, closure in sorted(
            groups, key=lambda g: (g[0].status != "withdrawn", g[0].kind.value, g[0].participants)
        ):
            try:
                candidate = StrategyFrame.model_validate(
                    {
                        **frame.model_dump(),
                        "situations": [*frame.situations, item],
                        "evidence": {**frame.evidence, **closure},
                    }
                )
            except ValidationError:
                frame.situations_truncated = True
                frame.omitted_situations += 1
                frame.capabilities[item.kind.value] = Capability(
                    availability="omitted", reason="byte_or_evidence_budget"
                )
                if "byte_budget" not in frame.limitations:
                    frame.limitations.append("byte_budget")
            else:
                frame = candidate
        return StrategyDelta(
            frame=frame,
            transitions=transitions,
            battle_contexts=contexts(session, pairs, context, frame),
        )

    @staticmethod
    def signature(kind, payload):
        data = payload.model_dump(mode="json")
        for tyre in data["tyres"]:
            tyre.pop("age_laps", None)
        data.pop("age_offset_laps", None)
        if payload.pace:
            data["pace"] = int(payload.pace.difference_seconds / 0.5)
        return hashlib.sha256(encoded(data)).hexdigest()
