// SPDX-License-Identifier: AGPL-3.0-only
//
// Driver track position state: per-driver merging, sample selection and
// interpolation. Everything here is pure so the map can be reasoned about (and
// tested) without a DOM, a network, or an animation frame.

import type { DriverLocationSample } from "@/lib/types";

/** One car's current position in raw provider coordinates. */
export type DriverLocationState = {
  driverNumber: number;
  x: number;
  y: number;
  z: number | null;
  timestamp: number;
  stale: boolean;
  interpolated: boolean;
};

export type SampleSeries = Map<number, DriverLocationSample[]>;

/**
 * A car covers ~100 m/s at racing speed. Anything implying more than 200 m/s
 * between two fixes is a discontinuity — a pit or garage transition, a replay
 * seek, or a bad row — not motion to animate across the circuit.
 */
const TELEPORT_UNITS_PER_MS = 2;

/** Beyond this gap the feed has stopped rather than slowed; hold, don't glide. */
export const DEFAULT_MAX_INTERPOLATION_GAP_MS = 5_000;

/** How long a car's newest fix stays trustworthy before it is marked stale. */
export const DEFAULT_STALE_AFTER_MS = 10_000;

export function sampleTime(sample: DriverLocationSample): number {
  return Date.parse(sample.sample_time);
}

function isUsable(sample: DriverLocationSample): boolean {
  return (
    Number.isFinite(sample.x) &&
    Number.isFinite(sample.y) &&
    Number.isFinite(sampleTime(sample))
  );
}

/**
 * Merge new fixes into a per-driver "latest known position" map.
 *
 * Only the drivers named in `incoming` change. A quiet car keeps its last
 * position instead of vanishing, and an out-of-order older fix never
 * overwrites a newer one.
 */
export function mergeLatestLocations(
  current: ReadonlyMap<number, DriverLocationSample>,
  incoming: readonly DriverLocationSample[],
): Map<number, DriverLocationSample> {
  const merged = new Map(current);
  for (const sample of incoming) {
    if (!isUsable(sample)) continue;
    const existing = merged.get(sample.driver_number);
    if (existing && sampleTime(existing) >= sampleTime(sample)) continue;
    merged.set(sample.driver_number, sample);
  }
  return merged;
}

/** Add fixes to the per-driver time series, keeping each series sorted. */
export function appendSamples(
  series: SampleSeries,
  incoming: readonly DriverLocationSample[],
): SampleSeries {
  const next: SampleSeries = new Map(series);
  const touched = new Set<number>();
  for (const sample of incoming) {
    if (!isUsable(sample)) continue;
    const existing = next.get(sample.driver_number);
    const list = existing ? existing.slice() : [];
    list.push(sample);
    next.set(sample.driver_number, list);
    touched.add(sample.driver_number);
  }
  for (const driverNumber of touched) {
    const list = next.get(driverNumber);
    if (!list) continue;
    list.sort((left, right) => sampleTime(left) - sampleTime(right));
    next.set(driverNumber, dedupeByTime(list));
  }
  return next;
}

function dedupeByTime(list: DriverLocationSample[]): DriverLocationSample[] {
  const result: DriverLocationSample[] = [];
  let previous = Number.NaN;
  for (const sample of list) {
    const time = sampleTime(sample);
    if (time === previous) continue;
    previous = time;
    result.push(sample);
  }
  return result;
}

/** Drop fixes older than `keepFromMs` so a long replay does not grow forever. */
export function pruneSeries(series: SampleSeries, keepFromMs: number): SampleSeries {
  const next: SampleSeries = new Map();
  for (const [driverNumber, list] of series) {
    const index = list.findIndex((sample) => sampleTime(sample) >= keepFromMs);
    // Keep one fix before the cut so a driver still has a position to hold.
    const start = index <= 0 ? 0 : index - 1;
    next.set(driverNumber, start === 0 ? list : list.slice(start));
  }
  return next;
}

type SelectOptions = {
  maxInterpolationGapMs?: number;
  staleAfterMs?: number;
};

/**
 * Positions for every driver at replay/live clock `atMs`.
 *
 * Selection is "newest fix at or before the clock", per driver — never an
 * exact timestamp match, because providers do not sample all cars together.
 * When the following fix is close enough in time and space, the pair is
 * interpolated; otherwise the earlier fix is held so a car snaps rather than
 * flying across the circuit.
 */
export function selectLocationsAt(
  series: SampleSeries,
  atMs: number,
  options: SelectOptions = {},
): DriverLocationState[] {
  const maxGap = options.maxInterpolationGapMs ?? DEFAULT_MAX_INTERPOLATION_GAP_MS;
  const staleAfter = options.staleAfterMs ?? DEFAULT_STALE_AFTER_MS;
  const states: DriverLocationState[] = [];

  for (const [driverNumber, list] of series) {
    if (!list.length) continue;
    const index = lastIndexAtOrBefore(list, atMs);
    if (index < 0) continue;
    const previous = list[index];
    const previousTime = sampleTime(previous);
    const next = list[index + 1];
    const state: DriverLocationState = {
      driverNumber,
      x: previous.x,
      y: previous.y,
      z: previous.z,
      timestamp: previousTime,
      stale: atMs - previousTime > staleAfter,
      interpolated: false,
    };

    if (next) {
      const nextTime = sampleTime(next);
      const span = nextTime - previousTime;
      const distance = Math.hypot(next.x - previous.x, next.y - previous.y);
      const teleport = span > 0 && distance / span > TELEPORT_UNITS_PER_MS;
      if (span > 0 && span <= maxGap && !teleport) {
        const ratio = Math.min(1, Math.max(0, (atMs - previousTime) / span));
        state.x = previous.x + (next.x - previous.x) * ratio;
        state.y = previous.y + (next.y - previous.y) * ratio;
        state.interpolated = ratio > 0;
        state.stale = false;
      }
    }
    states.push(state);
  }

  return states.sort((left, right) => left.driverNumber - right.driverNumber);
}

function lastIndexAtOrBefore(list: DriverLocationSample[], atMs: number): number {
  let low = 0;
  let high = list.length - 1;
  let found = -1;
  while (low <= high) {
    const mid = (low + high) >> 1;
    if (sampleTime(list[mid]) <= atMs) {
      found = mid;
      low = mid + 1;
    } else {
      high = mid - 1;
    }
  }
  return found;
}

/** Convert a merged latest-position map into render states. */
export function statesFromLatest(
  latest: ReadonlyMap<number, DriverLocationSample>,
  nowMs: number,
  staleAfterMs = DEFAULT_STALE_AFTER_MS,
): DriverLocationState[] {
  return [...latest.values()]
    .map((sample) => ({
      driverNumber: sample.driver_number,
      x: sample.x,
      y: sample.y,
      z: sample.z,
      timestamp: sampleTime(sample),
      stale: nowMs - sampleTime(sample) > staleAfterMs,
      interpolated: false,
    }))
    .sort((left, right) => left.driverNumber - right.driverNumber);
}

export function seriesSampleCount(series: SampleSeries): number {
  let total = 0;
  for (const list of series.values()) total += list.length;
  return total;
}

/**
 * Bounds of the loaded series, used only when the server has no derived
 * geometry. Percentile trimming keeps one garage fix from shrinking the
 * circuit into a corner.
 */
export function seriesTimeRange(series: SampleSeries): [number, number] | null {
  let first = Infinity;
  let last = -Infinity;
  for (const list of series.values()) {
    if (!list.length) continue;
    first = Math.min(first, sampleTime(list[0]));
    last = Math.max(last, sampleTime(list[list.length - 1]));
  }
  return Number.isFinite(first) && Number.isFinite(last) ? [first, last] : null;
}
