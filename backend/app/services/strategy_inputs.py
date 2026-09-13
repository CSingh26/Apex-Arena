# SPDX-License-Identifier: AGPL-3.0-only
"""Bounded source witnesses and validated estimate products; no public-state authority."""

from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass
from itertools import pairwise

from app.domain.models import RaceEventType as E
from app.domain.strategy import FactReference
from app.domain.strategy_situations import (
    DriverTyreContext,
    PaceWindow,
    RelativePaceContext,
    StrategyEvidence,
)
from app.services.strategy_estimates import estimate_pace, estimate_pit_loss


def number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def evidence(ref, session, role, family, driver=None, lap=None, basis="observed"):
    return StrategyEvidence(
        event_id=ref.event_id,
        sequence=ref.sequence,
        observed_at=ref.observed_at,
        source=ref.source,
        session_key=session,
        role=role,
        family=family,
        driver_number=driver,
        lap_number=lap,
        basis=basis,
    )


def source_ref(source):
    return FactReference(
        event_id=source.id,
        sequence=source.sequence_number,
        observed_at=source.event_time,
        source=source.source,
    )


@dataclass
class Witness:
    value: object
    ref: StrategyEvidence
    lap: int | None = None


class SourceIndex:
    def __init__(self):
        self.fields = {}
        self.intervals = defaultdict(lambda: deque(maxlen=16))
        self.positions = defaultdict(lambda: deque(maxlen=16))

    def advance(self, source):
        driver = source.primary_driver_number or (
            source.driver_numbers[0] if source.driver_numbers else None
        )
        if driver is None or (driver not in self.fields and len(self.fields) >= 64):
            return
        fields = self.fields.setdefault(driver, {})
        mappings = {
            E.POSITION_SAMPLE: [("position", "position")],
            E.INTERVAL_SAMPLE: [("interval", "interval"), ("gap_to_leader", "interval")],
            E.DRIVER_UPDATE: [("team_name", "team")],
            E.CAR_DATA_SAMPLE: [("drs", "drs")],
        }
        for field, role in mappings.get(source.event_type, []):
            if field not in source.payload:
                continue
            value = source.payload[field]
            if field == "team_name":
                value = value if isinstance(value, str) and 0 < len(value) <= 80 else None
            elif field == "position":
                value = value if type(value) is int and 1 <= value <= 64 else None
            else:
                value = number(value)
            witness = Witness(
                value,
                evidence(
                    source_ref(source), source.session_key, role, source.event_type.value, driver
                ),
                source.lap_number,
            )
            previous = fields.get(field)
            # Late facts cannot overwrite a newer observation; same-time corrections can.
            if previous and previous.ref.observed_at > witness.ref.observed_at:
                continue
            fields[field] = witness
            if field in {"interval", "position"}:
                ring = self.intervals[driver] if field == "interval" else self.positions[driver]
                if not ring or ring[-1].ref.event_id != witness.ref.event_id:
                    ring.append(witness)

    def pairs(self, active):
        positions = [
            (fields["position"].value, driver)
            for driver, fields in self.fields.items()
            if driver in active and fields.get("position") and fields["position"].value is not None
        ]
        counts = {pos: sum(p == pos for p, _ in positions) for pos, _ in positions}
        ordered = sorted((pos, driver) for pos, driver in positions if counts[pos] == 1)
        pairs = {(a[1], b[1]) for a, b in pairwise(ordered) if b[0] == a[0] + 1}
        teams = defaultdict(list)
        for _, driver in ordered:
            team = self.fields[driver].get("team_name")
            if team and team.value:
                teams[team.value].append(driver)
        for members in teams.values():
            pairs.update(pairwise(members))
        return sorted(pairs)[:126]

    @staticmethod
    def fresh(row, now):
        return (
            row is not None
            and row.value is not None
            and 0 <= (now - row.ref.observed_at).total_seconds() <= 30
        )

    def gap(self, pair, now):
        a, b = (self.fields.get(d, {}) for d in pair)
        rows = [a.get("position"), b.get("position"), b.get("interval")]
        if not all(self.fresh(row, now) for row in rows):
            return None
        if rows[1].value != rows[0].value + 1 or max(r.ref.observed_at for r in rows) - min(
            r.ref.observed_at for r in rows
        ) > __import__("datetime").timedelta(seconds=5):
            return None
        return rows[2].value, [r.ref for r in rows]

    def team(self, pair):
        rows = [self.fields.get(d, {}).get("team_name") for d in pair]
        if not all(r and r.value for r in rows):
            return None, []
        return rows[0].value == rows[1].value, [r.ref for r in rows]


@dataclass
class DriverProducts:
    tyre: DriverTyreContext
    tyre_refs: list
    pace: object
    pace_refs: list
    pace_times: tuple | None
    latest_lap: int | None
    loss: object = None
    loss_refs: list = None


def driver_products(driver, history, context, state):
    """Only called on relevant revisions. Never retain the borrowed history tree."""
    session = context.session_key
    stint = max(history.stints, key=lambda row: row.stint_number) if history.stints else None
    tyre = DriverTyreContext(
        driver_number=driver,
        compound=stint.compound if stint and stint.compound and len(stint.compound) <= 80 else None,
        stint_number=stint.stint_number if stint else None,
        age_laps=state.tyre_age_laps,
        age_basis=state.tyre_age_basis,
        stop_count=len(history.pits),
        stop_count_basis="retained_lower_bound"
        if context.history.pit_history_truncated
        else "complete",
    )
    tyre_refs = (
        [evidence(stint.evidence, session, "stint", E.STINT_UPDATE.value, driver)] if stint else []
    )
    for ref in state.tyre_age_evidence:
        if stint and ref.event_id == stint.evidence.event_id:
            continue
        tyre_refs.append(evidence(ref, session, "lap", E.LAP_COMPLETED.value, driver))
    pace = estimate_pace(history)
    pace_refs = []
    times = []
    for ref in pace.evidence:
        lap = next((row for row in history.laps if row.evidence.event_id == ref.event_id), None)
        if lap:
            pace_refs.append(
                evidence(ref, session, "lap", E.LAP_COMPLETED.value, driver, lap.lap_number)
            )
            times.append(ref.observed_at)
            for control in lap.control_evidence:
                pace_refs.append(evidence(control, session, "control", E.RACE_CONTROL.value))
        else:
            pace_refs.append(evidence(ref, session, "stint", E.STINT_UPDATE.value, driver))
    loss = None
    loss_refs = []
    for pit in sorted(history.pits, key=lambda p: p.lap_number, reverse=True):
        if state.completed_lap is None or not 0 <= state.completed_lap - pit.lap_number <= 20:
            continue
        candidate = estimate_pit_loss(history, pit.lap_number)
        if candidate.availability != "available":
            continue
        # Typed retained roles are resolved to this driver; all premise refs remain at H.
        roles = candidate.evidence_roles
        laps = [
            next((lap for lap in history.laps if lap.evidence == r), None)
            for r in roles.ordered()[:5]
        ]
        if any(
            lap is None
            or lap.deleted
            or lap.interval_start is None
            or lap.interval_end is None
            or not lap.control_evidence
            for lap in laps
        ):
            continue
        if any(ref.sequence > context.relevant_sequence for ref in candidate.evidence):
            continue
        weather = sorted(context.history.weather, key=lambda w: w.evidence.observed_at)
        rain = [
            w.rainfall
            for w in weather
            if w.evidence.observed_at >= pit.evidence.observed_at and w.rainfall is not None
        ]
        if len(set(rain)) > 1:
            continue
        loss = candidate
        for lap in laps:
            loss_refs.append(
                evidence(
                    lap.evidence, session, "lap", E.LAP_COMPLETED.value, driver, lap.lap_number
                )
            )
            loss_refs.extend(
                evidence(r, session, "control", E.RACE_CONTROL.value) for r in lap.control_evidence
            )
        loss_refs.append(
            evidence(pit.evidence, session, "pit", E.PIT_STOP.value, driver, pit.lap_number)
        )
        break
    return DriverProducts(
        tyre,
        tyre_refs,
        pace,
        pace_refs,
        (min(times), max(times)) if times else None,
        state.completed_lap,
        loss,
        loss_refs,
    )


def relative(first, second, now, *, historic_second=False):
    if (
        not first
        or not second
        or first.pace.availability != "available"
        or second.pace.availability != "available"
    ):
        return None
    for product in (first, second):
        if (
            product.pace_times is None
            or (now - product.pace_times[1]).total_seconds() > 300
            or product.latest_lap is None
            or product.latest_lap - max(product.pace.sample_laps) > 2
        ):
            return None
    if not historic_second and max(first.pace_times[0], second.pace_times[0]) > min(
        first.pace_times[1], second.pace_times[1]
    ):
        return None
    a, b = first.pace, second.pace

    def window(product):
        p = product.pace
        return PaceWindow(
            driver_number=product.tyre.driver_number,
            stint_number=p.stint_number,
            median_seconds=p.representative_seconds,
            range_seconds=p.observed_range_seconds,
            sample_laps=p.sample_laps,
        )

    return RelativePaceContext(
        first=window(first),
        second=window(second),
        difference_seconds=a.representative_seconds - b.representative_seconds,
        range_seconds=(
            a.observed_range_seconds[0] - b.observed_range_seconds[1],
            a.observed_range_seconds[1] - b.observed_range_seconds[0],
        ),
    )
