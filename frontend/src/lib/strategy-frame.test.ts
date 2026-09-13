// SPDX-License-Identifier: AGPL-3.0-only
import { describe, expect, it } from "vitest";

import {
  capabilityRows,
  evidenceFor,
  formatSeconds,
  formatSecondsRange,
  humaniseCode,
  isHedged,
  payloadRows,
  rankSituations,
  undeterminedLabels,
} from "@/lib/strategy-frame";
import {
  strategyEvidence,
  strategyFrame,
  strategyPayload,
  strategySituation,
} from "@/test/strategy-fixtures";

function valueFor(rows: ReturnType<typeof payloadRows>, label: string): string | undefined {
  return rows.find((row) => row.label === label)?.value;
}

describe("humaniseCode", () => {
  it("turns a backend reason code into a sentence", () => {
    expect(humaniseCode("missing_authoritative_remaining_distance")).toBe(
      "Missing authoritative remaining distance",
    );
  });

  it("never returns an empty label", () => {
    expect(humaniseCode("   ")).toBe("Unspecified");
  });
});

describe("second formatting", () => {
  it("formats sub-minute and minute-scale values in F1 convention", () => {
    expect(formatSeconds(1.284)).toBe("1.284s");
    expect(formatSeconds(92.481)).toBe("1:32.481s");
  });

  it("keeps the sign and renders zero honestly", () => {
    expect(formatSeconds(-0.4)).toBe("-0.400s");
    expect(formatSeconds(0)).toBe("0.000s");
  });

  it("reports unknown values as a dash rather than a fabricated number", () => {
    expect(formatSeconds(null)).toBe("—");
    expect(formatSeconds(Number.NaN)).toBe("—");
    expect(formatSecondsRange(null)).toBe("—");
    expect(formatSecondsRange([0.8, 1.6])).toBe("0.800s to 1.600s");
  });
});

describe("payloadRows", () => {
  it("renders every determined value with its unit and never raw milliseconds", () => {
    const rows = payloadRows(
      strategyPayload({
        gap_seconds: 2.031,
        required_gain_seconds: 1.284,
        remaining_laps: 18,
        track_temperature_change: 3.25,
        wind_speed_change: -1.5,
        neutralization: "safety_car",
        clean_laps_since_pit: 2,
        pit_window: {
          loss_seconds: 21.4,
          loss_range_seconds: [20.9, 22.1],
          projected_gap_seconds: [1.2, 3.4],
          traffic: [63],
          rank_range: [4, 6],
          timing_basis: "approximate_lap_interval",
        },
      }),
    );

    expect(valueFor(rows, "Gap")).toBe("+2.031");
    expect(valueFor(rows, "Required gain")).toBe("1.284s");
    expect(valueFor(rows, "Remaining laps")).toBe("18 laps");
    expect(valueFor(rows, "Clean laps since stop")).toBe("2 laps");
    expect(valueFor(rows, "Track temperature change")).toBe("+3.3 °C");
    expect(valueFor(rows, "Wind speed change")).toBe("-1.5 m/s");
    expect(valueFor(rows, "Neutralisation")).toBe("Safety car");
    expect(valueFor(rows, "Pit-lane loss")).toBe("21.40s");
    expect(valueFor(rows, "Projected rank range")).toBe("P4 to P6");
    expect(valueFor(rows, "Traffic in the window")).toBe("Car 63");
    expect(valueFor(rows, "Timing basis")).toBe("Approximate lap interval");
  });

  it("renders lap-time medians as lap times", () => {
    const rows = payloadRows(
      strategyPayload({
        pace: {
          first: { driver_number: 4, stint_number: 2, median_seconds: 92.481, range_seconds: [92.1, 92.9], sample_laps: [20, 21, 22] },
          second: { driver_number: 16, stint_number: 2, median_seconds: 92.881, range_seconds: [92.5, 93.2], sample_laps: [20, 21, 22] },
          difference_seconds: -0.4,
          range_seconds: [-0.8, -0.1],
          shared_conditions: "overlapping_green_samples",
        },
      }),
    );

    expect(valueFor(rows, "Car 4 median lap")).toBe("1:32.481 over 3 laps");
    expect(valueFor(rows, "Pace difference")).toBe("-0.400s");
    expect(valueFor(rows, "Shared conditions")).toBe("Overlapping green samples");
  });

  it("omits values the backend did not determine", () => {
    expect(payloadRows(strategyPayload())).toEqual([]);
  });
});

describe("undeterminedLabels", () => {
  it("lists the contract fields pinned to unknown", () => {
    expect(undeterminedLabels(strategyPayload())).toEqual([
      "Team plan",
      "Outcome",
      "Pace on new tyres",
      "Tyre warm-up",
      "Rival stop timing",
    ]);
  });
});

describe("evidenceFor", () => {
  it("resolves evidence in sequence order and reports unresolved keys", () => {
    const situation = strategySituation({
      evidence_keys: ["event-a:pit", "event-b:lap", "event-missing:stint"],
    });
    const frame = strategyFrame({
      situations: [situation],
      evidence: {
        "event-b:lap": strategyEvidence({ sequence: 41, role: "lap" }),
        "event-a:pit": strategyEvidence({ sequence: 39, role: "pit" }),
      },
    });

    const { rows, missingKeys } = evidenceFor(frame, situation);
    expect(rows.map((row) => row.key)).toEqual(["event-a:pit", "event-b:lap"]);
    expect(missingKeys).toEqual(["event-missing:stint"]);
  });
});

describe("capabilityRows", () => {
  it("always returns the eight contract capabilities, defaulting missing ones", () => {
    const frame = strategyFrame({ capabilities: {} });
    const rows = capabilityRows(frame);
    expect(rows).toHaveLength(8);
    expect(rows.every((row) => row.capability.availability === "unavailable")).toBe(true);
  });
});

describe("rankSituations", () => {
  it("puts active reads before withdrawals and orders by product relevance", () => {
    const ranked = rankSituations([
      strategySituation({ kind: "stint_divergence", revision_id: "a" }),
      strategySituation({ kind: "weather_change", revision_id: "b", status: "withdrawn" }),
      strategySituation({ kind: "weather_change", revision_id: "c" }),
    ]);
    expect(ranked.map((situation) => situation.revision_id)).toEqual(["c", "a", "b"]);
  });
});

describe("isHedged", () => {
  it("hedges qualified confidence and anything less than fully available", () => {
    expect(isHedged(strategySituation())).toBe(false);
    expect(isHedged(strategySituation({ observation_confidence: "qualified" }))).toBe(true);
    expect(isHedged(strategySituation({ availability: "partial" }))).toBe(true);
  });
});
