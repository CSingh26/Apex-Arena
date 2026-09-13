# SPDX-License-Identifier: AGPL-3.0-only
"""Conditional timing scenarios and bounded actual pit-cycle anchors."""

from copy import deepcopy
from dataclasses import dataclass

from app.domain.models import RaceEventType as E
from app.domain.strategy import FactReference
from app.domain.strategy_situations import PitWindowContext, SituationPayload
from app.services.strategy_inputs import evidence, relative, source_ref
from app.services.strategy_scenarios import StrategyTiming, project_pit_window


def timing_rows(index, context, now):
    rows, refs, approximate = {}, {}, set()
    for driver, fields in sorted(index.fields.items()):
        row = fields.get("gap_to_leader")
        if not index.fresh(row, now):
            continue
        lap = row.lap
        basis = []
        if lap is None:
            history = context.history.drivers.get(str(driver))
            matches = (
                [
                    lap_row
                    for lap_row in history.laps
                    if lap_row.interval_start is not None
                    and lap_row.interval_end is not None
                    and lap_row.interval_start <= row.ref.observed_at <= lap_row.interval_end
                ]
                if history
                else []
            )
            if len(matches) != 1:
                continue
            found = matches[0]
            lap = found.lap_number
            approximate.add(driver)
            basis = [
                evidence(
                    r,
                    context.session_key,
                    "lap",
                    E.LAP_COMPLETED.value,
                    driver,
                    lap,
                    basis="approximate_lap_interval",
                )
                for r in found.interval_evidence
            ]
        try:
            rows[driver] = StrategyTiming(
                driver_number=driver,
                gap_to_leader_seconds=row.value,
                lap_number=lap,
                evidence=FactReference(
                    **row.ref.model_dump(include={"event_id", "sequence", "observed_at", "source"})
                ),
            )
        except ValueError:
            continue
        refs[driver] = [row.ref, *basis]
    return rows, refs, approximate


def pit_windows(session, context, now, active):
    timings, refs, approximate = timing_rows(session.index, context, now)
    windows = {}
    for driver, product in session.products.items():
        if driver not in active or product.loss is None or driver not in timings:
            continue
        timing = timings[driver]
        rivals = [
            r
            for n, r in timings.items()
            if n != driver
            and r.lap_number == timing.lap_number
            and abs((r.evidence.observed_at - timing.evidence.observed_at).total_seconds()) <= 5
        ]
        if not rivals:
            continue
        scenario = project_pit_window(
            timing,
            rivals,
            product.loss,
            as_of=now,
            complete_field=False,
            neutralization=context.control.neutralization.value,
        )
        if scenario.projected_gap_range_seconds is None:
            continue
        involved = [driver, *[r.driver_number for r in rivals]]
        closure = [*product.loss_refs, *[ref for n in involved for ref in refs[n]]]
        current_control = context.control.neutralization.evidence
        if current_control:
            closure.append(
                evidence(current_control, context.session_key, "control", E.RACE_CONTROL.value)
            )
        window = PitWindowContext(
            loss_seconds=product.loss.seconds,
            loss_range_seconds=(product.loss.lower_seconds, product.loss.upper_seconds),
            projected_gap_seconds=scenario.projected_gap_range_seconds,
            traffic=scenario.traffic_driver_numbers,
            timing_basis="approximate_lap_interval"
            if any(n in approximate for n in involved)
            else "observed",
        )
        windows[driver] = window, closure, scenario.assumptions, scenario.limitations
    return windows, timings


@dataclass
class PitCycle:
    pit: object
    subject: int
    rival: int
    baseline: object
    refs: list


def capture_cycle(session, source, context, now, pairs):
    if source.event_type != E.PIT_STOP:
        return
    rival = source.primary_driver_number or (
        source.driver_numbers[0] if source.driver_numbers else None
    )
    session.cycles = {k: v for k, v in session.cycles.items() if v.subject != rival}
    if context.control.neutralization.value != "green":
        return
    for pair in pairs:
        if rival not in pair:
            continue
        subject = pair[0] if pair[1] == rival else pair[1]
        first, second = session.products.get(subject), session.products.get(rival)
        gap = session.index.gap(pair, now)
        if gap is None or relative(first, second, now) is None:
            continue
        ref = evidence(
            source_ref(source),
            source.session_key,
            "pit",
            E.PIT_STOP.value,
            rival,
            source.lap_number,
        )
        session.cycles[(subject, rival)] = PitCycle(
            ref,
            subject,
            rival,
            deepcopy(second),
            [*gap[1], *second.pace_refs, *second.tyre_refs, ref],
        )


def cycle_candidates(session, context, now, active):
    candidates = []
    for key, cycle in list(session.cycles.items()):
        history = context.history.drivers.get(str(cycle.subject))
        rows = (
            [
                lap_row
                for lap_row in history.laps
                if lap_row.evidence.sequence > cycle.pit.sequence
                and not lap_row.deleted
                and not lap_row.exclusions
            ]
            if history
            else []
        )
        if (
            cycle.subject not in active
            or context.control.neutralization.value != "green"
            or (now - cycle.pit.observed_at).total_seconds() > 600
            or len(rows) > 5
        ):
            del session.cycles[key]
            continue
        if not rows:
            continue
        own = session.products.get(cycle.subject)
        pace = relative(own, cycle.baseline, now, historic_second=True)
        if pace is None:
            continue
        candidates.append(
            (
                "overcut_condition",
                key,
                SituationPayload(
                    pace=pace, pit_anchor=cycle.pit.event_id, clean_laps_since_pit=len(rows)
                ),
                [*cycle.refs, *own.pace_refs],
                ["rival_pre_stop_baseline", "observed_stay_out_not_proved_overcut"],
                ["traffic_unknown", "warmup_unknown", "next_stop_unknown"],
            )
        )
    return candidates
