// SPDX-License-Identifier: AGPL-3.0-only
import { describe, expect, it } from "vitest";

import {
  appendSamples,
  mergeLatestLocations,
  pruneSeries,
  selectLocationsAt,
  seriesSampleCount,
  statesFromLatest,
  type SampleSeries,
} from "@/lib/driver-locations";
import type { DriverLocationSample } from "@/lib/types";

const BASE = Date.parse("2026-07-19T13:00:00.000Z");

function sample(driver: number, seconds: number, x: number, y: number): DriverLocationSample {
  return {
    driver_number: driver,
    x,
    y,
    z: 10,
    sample_time: new Date(BASE + seconds * 1000).toISOString(),
  };
}

function seriesOf(...samples: DriverLocationSample[]): SampleSeries {
  return appendSamples(new Map(), samples);
}

describe("mergeLatestLocations", () => {
  it("updates only the drivers in the incoming batch", () => {
    const current = mergeLatestLocations(new Map(), [
      sample(1, 0, 10, 10),
      sample(4, 0, 20, 20),
      sample(16, 0, 30, 30),
    ]);
    const merged = mergeLatestLocations(current, [sample(4, 1, 99, 99)]);
    expect(merged.size).toBe(3);
    expect(merged.get(1)?.x).toBe(10);
    expect(merged.get(16)?.x).toBe(30);
    expect(merged.get(4)?.x).toBe(99);
  });

  it("does not clear other drivers when one car updates", () => {
    let latest = mergeLatestLocations(new Map(), [sample(1, 0, 1, 1), sample(4, 0, 2, 2)]);
    latest = mergeLatestLocations(latest, [sample(16, 5, 3, 3)]);
    expect([...latest.keys()].sort((a, b) => a - b)).toEqual([1, 4, 16]);
  });

  it("ignores an out-of-order older sample", () => {
    const current = mergeLatestLocations(new Map(), [sample(1, 10, 100, 100)]);
    const merged = mergeLatestLocations(current, [sample(1, 5, 50, 50)]);
    expect(merged.get(1)?.x).toBe(100);
  });

  it("drops malformed samples", () => {
    const merged = mergeLatestLocations(new Map(), [
      { driver_number: 1, x: NaN, y: 5, z: null, sample_time: new Date(BASE).toISOString() },
      { driver_number: 4, x: 1, y: 2, z: null, sample_time: "not-a-date" },
    ]);
    expect(merged.size).toBe(0);
  });
});

describe("selectLocationsAt", () => {
  it("takes the newest fix at or before the clock, per driver", () => {
    const series = seriesOf(
      sample(1, 0, 0, 0),
      sample(1, 10, 100, 0),
      sample(4, 3, 30, 30),
      sample(4, 20, 200, 200),
    );
    // No driver sampled at exactly t+6: exact matching would return nothing.
    const states = selectLocationsAt(series, BASE + 6_000, { maxInterpolationGapMs: 0 });
    const byDriver = new Map(states.map((state) => [state.driverNumber, state]));
    expect(byDriver.get(1)?.x).toBe(0);
    expect(byDriver.get(4)?.x).toBe(30);
  });

  it("interpolates between the bracketing fixes", () => {
    const series = seriesOf(sample(1, 0, 0, 0), sample(1, 2, 200, 100));
    const state = selectLocationsAt(series, BASE + 1_000)[0];
    expect(state.x).toBeCloseTo(100, 5);
    expect(state.y).toBeCloseTo(50, 5);
    expect(state.interpolated).toBe(true);
  });

  it("snaps rather than animating an impossible jump", () => {
    // 40 km in one second is a garage transition or a seek, not motion.
    const series = seriesOf(sample(1, 0, 0, 0), sample(1, 1, 400_000, 0));
    const state = selectLocationsAt(series, BASE + 500)[0];
    expect(state.x).toBe(0);
    expect(state.interpolated).toBe(false);
  });

  it("holds position and marks stale when the feed stops", () => {
    const series = seriesOf(sample(1, 0, 42, 42));
    const state = selectLocationsAt(series, BASE + 30_000, { staleAfterMs: 10_000 })[0];
    expect(state.x).toBe(42);
    expect(state.stale).toBe(true);
  });

  it("does not extrapolate beyond the newest fix", () => {
    const series = seriesOf(sample(1, 0, 0, 0), sample(1, 1, 100, 0));
    const state = selectLocationsAt(series, BASE + 60_000)[0];
    expect(state.x).toBe(100);
  });

  it("skips a driver with no fix yet at this clock", () => {
    const series = seriesOf(sample(1, 0, 1, 1), sample(4, 50, 2, 2));
    const states = selectLocationsAt(series, BASE + 10_000);
    expect(states.map((state) => state.driverNumber)).toEqual([1]);
  });

  it("renders whatever cars exist rather than demanding a full grid", () => {
    const series = seriesOf(sample(1, 0, 1, 1), sample(4, 0, 2, 2), sample(16, 0, 3, 3));
    expect(selectLocationsAt(series, BASE).length).toBe(3);
  });

  it("holds instead of gliding across a long feed gap", () => {
    const series = seriesOf(sample(1, 0, 0, 0), sample(1, 60, 6_000, 0));
    const state = selectLocationsAt(series, BASE + 30_000, { maxInterpolationGapMs: 5_000 })[0];
    expect(state.x).toBe(0);
    expect(state.interpolated).toBe(false);
  });
});

describe("appendSamples", () => {
  it("keeps each driver's series sorted and de-duplicated", () => {
    let series = seriesOf(sample(1, 5, 50, 0), sample(1, 0, 0, 0));
    series = appendSamples(series, [sample(1, 5, 50, 0), sample(1, 2, 20, 0)]);
    const times = series.get(1)?.map((item) => Date.parse(item.sample_time)) ?? [];
    expect(times).toEqual([BASE, BASE + 2_000, BASE + 5_000]);
  });

  it("counts across drivers", () => {
    const series = seriesOf(sample(1, 0, 0, 0), sample(4, 0, 0, 0), sample(4, 1, 1, 1));
    expect(seriesSampleCount(series)).toBe(3);
  });
});

describe("pruneSeries", () => {
  it("keeps one fix before the cut so a car still has a position", () => {
    const series = seriesOf(sample(1, 0, 0, 0), sample(1, 10, 10, 0), sample(1, 20, 20, 0));
    const pruned = pruneSeries(series, BASE + 15_000);
    const kept = pruned.get(1) ?? [];
    expect(kept.length).toBe(2);
    expect(selectLocationsAt(pruned, BASE + 12_000)[0].x).toBe(10);
  });
});

describe("statesFromLatest", () => {
  it("marks a quiet car stale without dropping it", () => {
    const latest = mergeLatestLocations(new Map(), [sample(1, 0, 5, 5)]);
    const [state] = statesFromLatest(latest, BASE + 20_000, 10_000);
    expect(state.driverNumber).toBe(1);
    expect(state.stale).toBe(true);
  });
});
