// SPDX-License-Identifier: AGPL-3.0-only
import { describe, expect, it } from "vitest";

import {
  battleContextRows,
  battleEvidenceRows,
  battleFanCaveat,
  battleHighlights,
  battleUndeterminedLabels,
  formatApproximateDuration,
  prominenceRows,
} from "@/lib/battle-context";
import { battleContext, battleProminence, driverName, strategyEvidence } from "@/test/strategy-fixtures";

function rowValue(rows: { label: string; value: string }[], label: string): string | undefined {
  return rows.find((row) => row.label === label)?.value;
}

describe("battleContextRows", () => {
  it("renders determined values with units and F1 timing formatting", () => {
    const rows = battleContextRows(
      battleContext({
        duration_seconds: 94.5,
        closing: true,
        within_one_second: true,
        observed_wing_open: true,
        drs_permission: "enabled",
        remaining_laps: 12,
        same_reported_team: false,
        train_members: [4, 16, 63],
      }),
    );

    expect(rowValue(rows, "Battle duration")).toBe("1:34.500s");
    expect(rowValue(rows, "Gap trend")).toBe("Closing");
    expect(rowValue(rows, "Within one second (proximity)")).toBe("Yes");
    expect(rowValue(rows, "Rear wing observed open")).toBe("Yes");
    expect(rowValue(rows, "DRS permission (track state)")).toBe("Enabled");
    expect(rowValue(rows, "Remaining laps")).toBe("12 laps");
    expect(rowValue(rows, "Same reported team")).toBe("No");
    expect(rowValue(rows, "Train members")).toBe("Car 4, Car 16, Car 63");
  });

  it("omits a row for every value the engine left undetermined", () => {
    const rows = battleContextRows(battleContext());

    expect(rowValue(rows, "Battle duration")).toBeUndefined();
    expect(rowValue(rows, "Within one second (proximity)")).toBeUndefined();
    expect(rowValue(rows, "Rear wing observed open")).toBeUndefined();
    expect(rowValue(rows, "DRS permission (track state)")).toBeUndefined();
    expect(rowValue(rows, "Remaining laps")).toBeUndefined();
    expect(rowValue(rows, "Train members")).toBeUndefined();
  });

  it("never reports an unobserved wing as an observation of a closed wing", () => {
    const rows = battleContextRows(battleContext({ observed_wing_open: false }));
    expect(rowValue(rows, "Rear wing observed open")).toBe("Not observed open");
  });

  it("states a non-closing gap rather than leaving the trend blank", () => {
    const rows = battleContextRows(battleContext({ closing: false }));
    expect(rowValue(rows, "Gap trend")).toBe("Not closing at this cursor");
  });

  it("reuses the shared payload reader for tyre and pace context", () => {
    const rows = battleContextRows(
      battleContext({
        tyres: [
          {
            driver_number: 4,
            compound: "MEDIUM",
            stint_number: 2,
            age_laps: 6,
            age_basis: "observed",
            stop_count: 1,
            stop_count_basis: "complete",
          },
        ],
      }),
    );

    expect(rowValue(rows, "Car 4 tyre")).toContain("MEDIUM");
    expect(rowValue(rows, "Car 4 tyre")).toContain("6 laps old");
  });
});

describe("battleUndeterminedLabels", () => {
  it("always lists the two fields the contract pins to unavailable", () => {
    const labels = battleUndeterminedLabels(
      battleContext({
        duration_seconds: 30,
        within_one_second: true,
        observed_wing_open: true,
        drs_permission: "enabled",
        remaining_laps: 4,
        same_reported_team: true,
        tyres: [],
        pace: null,
      }),
    );

    expect(labels).toContain("Overtake attempts");
    expect(labels).toContain("Championship relevance");
  });

  it("never phrases an undetermined field as a negative fact", () => {
    const labels = battleUndeterminedLabels(battleContext());
    const joined = labels.join(" ").toLowerCase();

    expect(joined).not.toContain("no overtake");
    expect(joined).not.toContain("not championship");
    expect(labels).toContain("DRS permission (track state)");
    expect(labels).toContain("Within one second (proximity)");
    expect(labels).toContain("Rear wing observed open");
    expect(labels).toContain("Same reported team");
    expect(labels).toContain("Battle duration");
  });

  it("drops a field from the undetermined list once the engine determines it", () => {
    const labels = battleUndeterminedLabels(
      battleContext({ within_one_second: false, observed_wing_open: false }),
    );

    expect(labels).not.toContain("Within one second (proximity)");
    expect(labels).not.toContain("Rear wing observed open");
  });
});

describe("battleHighlights", () => {
  const input = { leadDriverNumber: 16, chasingDriverNumber: 4, driverName };

  it("explains a closing, close, same-team fight in plain words", () => {
    const highlights = battleHighlights({
      ...input,
      context: battleContext({
        closing: true,
        within_one_second: true,
        same_reported_team: true,
        duration_seconds: 150,
        train_members: [16, 4, 63],
        remaining_laps: 1,
      }),
    });
    const text = highlights.map((highlight) => highlight.text);

    expect(text).toContain("Lando Norris is taking time out of the car ahead.");
    expect(text).toContain("The two are running inside a second of each other.");
    expect(text).toContain("They are team mates, so the pit wall has to referee this one.");
    expect(text).toContain("This fight has been running for 2 minutes 30 seconds.");
    expect(text).toContain("3 cars are stacked up in the same train.");
    expect(text).toContain("1 lap remains to settle it.");
  });

  it("never phrases proximity or a track DRS state as a driver using DRS", () => {
    const highlights = battleHighlights({
      ...input,
      context: battleContext({ within_one_second: true, drs_permission: "enabled" }),
    });
    const joined = highlights.map((highlight) => highlight.text).join(" ").toLowerCase();

    expect(joined).toContain("inside a second");
    expect(joined).not.toContain("drs attack");
    expect(joined).not.toContain("using drs");
    expect(joined).not.toContain("with drs");
  });

  it("reports an observed open rear wing as the observation it is", () => {
    const highlights = battleHighlights({
      ...input,
      context: battleContext({ observed_wing_open: true }),
    });

    expect(highlights.map((highlight) => highlight.text)).toContain(
      "Lando Norris's rear wing has been observed open on this straight.",
    );
  });

  it("says nothing about a wing the engine never observed open", () => {
    const highlights = battleHighlights({ ...input, context: battleContext({ observed_wing_open: false }) });
    expect(highlights.map((highlight) => highlight.id)).not.toContain("wing");
  });

  it("derives a tyre offset only when both ages were observed", () => {
    const tyre = (driver_number: number, age_laps: number | null) => ({
      driver_number,
      compound: driver_number === 16 ? "HARD" : "MEDIUM",
      stint_number: 2,
      age_laps,
      age_basis: "observed",
      stop_count: 1,
      stop_count_basis: "complete" as const,
    });

    const both = battleHighlights({
      ...input,
      context: battleContext({ tyres: [tyre(16, 18), tyre(4, 6)] }),
    });
    expect(both.map((highlight) => highlight.text)).toContain(
      "Lando Norris is on tyres 12 laps fresher than Charles Leclerc's.",
    );

    const partial = battleHighlights({
      ...input,
      context: battleContext({ tyres: [tyre(16, 18), tyre(4, null)] }),
    });
    expect(partial.map((highlight) => highlight.text)).toContain(
      "Charles Leclerc is on the Hard and Lando Norris on the Medium.",
    );
  });

  it("returns nothing at all when the engine determined nothing", () => {
    expect(battleHighlights({ ...input, context: battleContext() })).toEqual([]);
  });
});

describe("battleFanCaveat", () => {
  it("names what cannot be told rather than implying a negative", () => {
    const caveat = battleFanCaveat(battleContext());
    expect(caveat).toBe(
      "We cannot tell whether a move has been attempted, "
        + "what this fight means for the championship or whether DRS has been used.",
    );
  });

  it("drops the DRS clause once a wing observation exists", () => {
    expect(battleFanCaveat(battleContext({ observed_wing_open: false }))).toBe(
      "We cannot tell whether a move has been attempted or "
        + "what this fight means for the championship.",
    );
  });
});

describe("prominenceRows", () => {
  it("reports the published components and total without recomputing them", () => {
    const rows = prominenceRows(battleProminence({ interval: 30, score: 81, basis: "not_racing" }));

    expect(rowValue(rows, "Interval")).toBe("30");
    expect(rowValue(rows, "Score")).toBe("81");
    expect(rowValue(rows, "Basis")).toBe("Not racing");
  });
});

describe("battleEvidenceRows", () => {
  it("orders evidence by published sequence", () => {
    const rows = battleEvidenceRows(
      battleContext({
        evidence: {
          late: strategyEvidence({ sequence: 90 }),
          early: strategyEvidence({ sequence: 12 }),
        },
      }),
    );

    expect(rows.map((row) => row.key)).toEqual(["early", "late"]);
  });

  it("is empty when the context closed over no evidence", () => {
    expect(battleEvidenceRows(battleContext())).toEqual([]);
  });
});

describe("formatApproximateDuration", () => {
  it("reads as words, never as milliseconds or a raw float", () => {
    expect(formatApproximateDuration(1)).toBe("1 second");
    expect(formatApproximateDuration(45.4)).toBe("45 seconds");
    expect(formatApproximateDuration(120)).toBe("2 minutes");
    expect(formatApproximateDuration(151)).toBe("2 minutes 31 seconds");
  });

  it("returns nothing for an undetermined or impossible duration", () => {
    expect(formatApproximateDuration(null)).toBeNull();
    expect(formatApproximateDuration(0)).toBeNull();
    expect(formatApproximateDuration(Number.NaN)).toBeNull();
  });
});
