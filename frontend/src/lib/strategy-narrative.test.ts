// SPDX-License-Identifier: AGPL-3.0-only
import { describe, expect, it } from "vitest";

import { buildFanNarrative, FAN_SITUATION_LIMIT } from "@/lib/strategy-narrative";
import {
  controlProjection,
  driverName,
  strategyFrame,
  strategyPayload,
  strategySituation,
} from "@/test/strategy-fixtures";
import type { BattleState } from "@/lib/types";

function texts(statements: { text: string }[]): string {
  return statements.map((statement) => statement.text).join(" | ");
}

function battle(overrides: Partial<BattleState> = {}): BattleState {
  return {
    id: "battle-1",
    session_key: "race-1",
    lead_driver_number: 16,
    chasing_driver_number: 4,
    lead_position: 3,
    chasing_position: 4,
    interval_seconds: 0.482,
    closest_interval_seconds: 0.41,
    interval_history: [0.9, 0.6, 0.482],
    started_at: "2026-08-10T13:15:00Z",
    last_updated_at: "2026-08-10T13:20:00Z",
    trend: "CLOSING",
    intensity: "INTENSE",
    status: "ACTIVE",
    within_one_second: true,
    drs_status: "enabled",
    tyre_context: {},
    lap_number: 24,
    train_size: 2,
    resolution_reason: null,
    ...overrides,
  };
}

describe("buildFanNarrative", () => {
  it("answers all three questions in plain language from a populated frame", () => {
    const narrative = buildFanNarrative({
      frame: strategyFrame(),
      control: controlProjection(),
      driverName,
    });

    expect(texts(narrative.happening)).toContain(
      "Lando Norris is in position to try an undercut on Charles Leclerc by stopping first.",
    );
    expect(texts(narrative.matters)).toContain(
      "To make it work Lando Norris needs to find about 1.284s",
    );
    expect(texts(narrative.watch)).toContain(
      "Watch whether Charles Leclerc covers the stop on the very next lap.",
    );
  });

  it("keeps prose free of raw millisecond or coefficient dumps", () => {
    const narrative = buildFanNarrative({ frame: strategyFrame(), driverName });
    const prose = [...narrative.happening, ...narrative.matters, ...narrative.watch]
      .map((statement) => statement.text)
      .join(" ");
    expect(prose).not.toMatch(/\d{4,}/);
    expect(prose).not.toMatch(/coefficient|%/i);
  });

  it("hedges the wording itself when a situation is only qualified", () => {
    const narrative = buildFanNarrative({
      frame: strategyFrame({
        situations: [
          strategySituation({
            kind: "weather_change",
            observation_confidence: "qualified",
            participants: [],
            payload: strategyPayload({ rainfall_before: false, rainfall_now: true }),
          }),
        ],
      }),
      driverName,
    });

    expect(narrative.happening[0].hedged).toBe(true);
    expect(narrative.happening[0].text).toBe("Rain appears to have started falling.");
  });

  it("hedges a partially available situation too", () => {
    const narrative = buildFanNarrative({
      frame: strategyFrame({
        situations: [strategySituation({ availability: "partial" })],
      }),
      driverName,
    });

    expect(narrative.happening[0].hedged).toBe(true);
    expect(narrative.happening[0].text).toContain("may be in position to try an undercut");
  });

  it("tells a withdrawn situation as a revision rather than dropping it", () => {
    const narrative = buildFanNarrative({
      frame: strategyFrame({
        situations: [
          strategySituation({
            status: "withdrawn",
            transition: "withdrawn",
            limitations: ["pit_window_closed"],
          }),
        ],
      }),
      driverName,
    });

    expect(narrative.happening).toHaveLength(1);
    expect(narrative.happening[0].tone).toBe("revision");
    expect(narrative.happening[0].text).toContain("Earlier read:");
    expect(narrative.happening[0].text).toContain("That no longer holds because pit window closed.");
    // A withdrawn read must not keep advising the fan to act on it.
    expect(narrative.watch).toHaveLength(0);
    expect(narrative.matters).toHaveLength(0);
  });

  it("marks a revised situation as an update", () => {
    const narrative = buildFanNarrative({
      frame: strategyFrame({ situations: [strategySituation({ transition: "revised" })] }),
      driverName,
    });
    expect(narrative.happening[0].tone).toBe("update");
    expect(narrative.happening[0].text).toContain("Updated read:");
  });

  it("surfaces truncated control history as an explicit uncertainty", () => {
    const narrative = buildFanNarrative({
      frame: strategyFrame(),
      control: controlProjection({ history_truncated: true }),
      driverName,
    });

    expect(narrative.caveats).toContain(
      "Race control history was truncated, so how many clean laps a car has had since its stop is uncertain.",
    );
  });

  it("reports truncated, omitted, suppressed and blocked capability counts", () => {
    const narrative = buildFanNarrative({
      frame: strategyFrame({
        situations_truncated: true,
        omitted_situations: 3,
        suppressed_events: 2,
        limitations: ["projection_not_current"],
        capabilities: {
          ...strategyFrame().capabilities,
          extra_stop_consequence: {
            availability: "unavailable",
            reason: "missing_authoritative_remaining_distance",
          },
        },
      }),
      driverName,
    });

    expect(narrative.caveats.join(" ")).toContain("3 were left out entirely");
    expect(narrative.caveats.join(" ")).toContain("2 source events were suppressed");
    expect(narrative.caveats).toContain("Projection not current.");
    expect(narrative.caveats.join(" ")).toContain("1 of the 8 strategy checks cannot run");
  });

  it("defers overflow reads to Analyst mode instead of dropping them silently", () => {
    const situations = Array.from({ length: FAN_SITUATION_LIMIT + 2 }, (_unused, index) =>
      strategySituation({
        revision_id: `revision-${index}`,
        sequence: 42 - index,
        participants: [index + 1, index + 20],
      }),
    );
    const narrative = buildFanNarrative({ frame: strategyFrame({ situations }), driverName });

    expect(narrative.happening).toHaveLength(FAN_SITUATION_LIMIT);
    expect(narrative.caveats).toContain("2 further strategy reads are listed in Analyst mode.");
  });

  it("reports a neutralised track and the cheap-stop consequence", () => {
    const narrative = buildFanNarrative({
      frame: strategyFrame({ situations: [] }),
      control: controlProjection({
        neutralization: { value: "safety_car", evidence: null },
      }),
      driverName,
    });

    expect(texts(narrative.happening)).toContain("The track is under safety car right now");
    expect(texts(narrative.watch)).toContain("stopping while the field is slowed is much cheaper");
  });

  it("turns live battles into watch-next prompts with F1-formatted gaps", () => {
    const narrative = buildFanNarrative({
      frame: strategyFrame({ situations: [] }),
      control: controlProjection(),
      battles: [battle(), battle({ id: "resolved", status: "RESOLVED" })],
      driverName,
    });

    expect(texts(narrative.watch)).toContain(
      "Lando Norris is 0.482s behind Charles Leclerc in the fight for P3",
    );
    expect(narrative.watch).toHaveLength(1);
  });

  it("produces nothing rather than something invented when there is no frame", () => {
    const narrative = buildFanNarrative({ frame: null, driverName });
    expect(narrative.happening).toEqual([]);
    expect(narrative.matters).toEqual([]);
    expect(narrative.watch).toEqual([]);
    expect(narrative.caveats).toEqual([]);
  });
});
