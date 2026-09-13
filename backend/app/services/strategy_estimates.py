# SPDX-License-Identifier: AGPL-3.0-only
"""Conservative descriptive estimates, never universal tyre or pit predictions."""

from __future__ import annotations

from statistics import median

from pydantic import ValidationError

from app.domain.strategy import (
    DriverHistory,
    LapObservation,
    PaceEstimate,
    PitLossEstimate,
    PitLossEvidenceRoles,
)


def _clean(lap: LapObservation) -> bool:
    return lap.duration_seconds is not None and not lap.deleted and not lap.exclusions


def estimate_pace(driver: DriverHistory) -> PaceEstimate:
    try:
        driver = DriverHistory.model_validate(driver.model_dump())
    except ValidationError:
        return PaceEstimate()
    if not driver.stints:
        return PaceEstimate()
    stint = max(driver.stints, key=lambda row: row.stint_number)
    rows = [
        lap
        for lap in driver.laps
        if lap.lap_number >= stint.lap_start
        and (stint.lap_end is None or lap.lap_number <= stint.lap_end)
    ]
    rows.sort(key=lambda row: row.lap_number)
    clean = [row for row in rows if _clean(row)][-12:]
    rejected = {row.lap_number for row in rows if not _clean(row)}
    if clean:
        center = median(row.duration_seconds for row in clean)
        mad = median(abs(row.duration_seconds - center) for row in clean)
        # A robust descriptive filter, not a universal degradation threshold.
        bound = max(3.0, 4 * mad)
        rejected.update(
            row.lap_number for row in clean if abs(row.duration_seconds - center) > bound
        )
        clean = [row for row in clean if row.lap_number not in rejected]
    common = {
        "stint_number": stint.stint_number,
        "sample_laps": [row.lap_number for row in clean],
        "excluded_laps": sorted(rejected),
        "evidence": [row.evidence for row in clean] + [stint.evidence],
    }
    if len(clean) < 3:
        return PaceEstimate(**common)
    durations = [row.duration_seconds for row in clean]
    metrics = {
        "availability": "available",
        "representative_seconds": median(durations),
        "observed_range_seconds": (min(durations), max(durations)),
    }
    if len(clean) >= 6:
        slopes = [
            (later.duration_seconds - earlier.duration_seconds)
            / (later.lap_number - earlier.lap_number)
            for i, earlier in enumerate(clean)
            for later in clean[i + 1 :]
        ]
        metrics["pace_drift_seconds_per_lap"] = median(slopes)
        metrics["drift_range_seconds_per_lap"] = (min(slopes), max(slopes))
    return PaceEstimate(**common, **metrics)


def estimate_pit_loss(driver: DriverHistory, pit_lap: int) -> PitLossEstimate:
    if isinstance(pit_lap, bool) or not isinstance(pit_lap, int) or not 1 <= pit_lap <= 100_000:
        return PitLossEstimate()
    try:
        driver = DriverHistory.model_validate(driver.model_dump())
    except ValidationError:
        return PitLossEstimate()
    pit = next((row for row in driver.pits if row.lap_number == pit_lap), None)
    in_lap = next((row for row in driver.laps if row.lap_number == pit_lap), None)
    out_lap = next((row for row in driver.laps if row.lap_number == pit_lap + 1), None)
    baseline = sorted(
        [row for row in driver.laps if row.lap_number < pit_lap and _clean(row)],
        key=lambda row: row.lap_number,
    )[-3:]
    if pit is None or in_lap is None or out_lap is None or len(baseline) != 3:
        return PitLossEstimate()
    if any(
        row.deleted or row.duration_seconds is None or set(row.exclusions) - {"pit_in", "pit_out"}
        for row in (in_lap, out_lap)
    ):
        return PitLossEstimate()
    if "pit_out" not in out_lap.exclusions or baseline[0].lap_number < pit_lap - 5:
        return PitLossEstimate()
    durations = [row.duration_seconds for row in baseline]
    loss = in_lap.duration_seconds + out_lap.duration_seconds - 2 * median(durations)
    if loss <= 0:
        return PitLossEstimate()
    sensitivity = max(1.0, 2 * (max(durations) - min(durations)))
    evidence = [
        *(row.evidence for row in baseline),
        in_lap.evidence,
        out_lap.evidence,
        pit.evidence,
    ]
    try:
        return PitLossEstimate(
            availability="available",
            seconds=loss,
            lower_seconds=max(0.0, loss - sensitivity),
            upper_seconds=loss + sensitivity,
            evidence=evidence,
            evidence_roles=PitLossEvidenceRoles(
                baseline_laps=(evidence[0], evidence[1], evidence[2]),
                in_lap=evidence[3],
                out_lap=evidence[4],
                pit=evidence[5],
            ),
        )
    except ValidationError:
        return PitLossEstimate()
