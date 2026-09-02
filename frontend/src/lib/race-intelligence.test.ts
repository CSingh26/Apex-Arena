// SPDX-License-Identifier: AGPL-3.0-only
import { describe, expect, it } from "vitest";

import {
  describeRecentChanges,
  filterRaceEvents,
  mergeSessionIntelligence,
  rankBattleCards,
} from "./race-intelligence";
import type { BattleState, NormalizedRaceEvent, SessionIntelligenceState } from "./types";

function event(
  id: string,
  sequence: number,
  eventType: string,
  drivers: number[] = [],
): NormalizedRaceEvent {
  return {
    id,
    session_key: "11334",
    source: "apexarena",
    raw_event_id: null,
    event_time: `2026-07-19T13:${String(sequence).padStart(2, "0")}:00Z`,
    received_at: "2026-07-19T13:00:00Z",
    processed_at: "2026-07-19T13:00:00Z",
    sequence_number: sequence,
    event_type: eventType,
    event_origin: "DERIVED",
    driver_numbers: drivers,
    primary_driver_number: drivers[0] ?? null,
    secondary_driver_number: drivers[1] ?? null,
    position_before: null,
    position_after: null,
    gap_seconds: null,
    interval_seconds: null,
    lap_number: sequence,
    importance: 0.8,
    importance_level: "IMPORTANT",
    confidence: 0.9,
    confidence_level: "HIGH",
    derivation: null,
    payload: {},
    dedup_key: `event-${id}`,
    is_replay: true,
  };
}

function battle(
  id: string,
  lead: number,
  chaser: number,
  interval: number,
  intensity: BattleState["intensity"] = "CLOSE",
): BattleState {
  return {
    id,
    session_key: "11334",
    lead_driver_number: lead,
    chasing_driver_number: chaser,
    lead_position: 4,
    chasing_position: 5,
    interval_seconds: interval,
    closest_interval_seconds: interval,
    interval_history: [interval + 0.2, interval],
    started_at: "2026-07-19T13:00:00Z",
    last_updated_at: "2026-07-19T13:01:00Z",
    trend: "CLOSING",
    intensity,
    status: "ACTIVE",
    within_one_second: interval <= 1,
    drs_status: "UNAVAILABLE",
    tyre_context: {},
    lap_number: 20,
    train_size: 2,
    resolution_reason: null,
  };
}

function intelligence(sequence: number, events: NormalizedRaceEvent[]): SessionIntelligenceState {
  return {
    session_key: "11334",
    sequence_number: sequence,
    current_battles: [],
    recent_events: events,
    qualifying: null,
  };
}

describe("race intelligence reducers", () => {
  it("keeps the newest state, deduplicates event IDs and bounds history", () => {
    const current = intelligence(10, [event("same", 9, "OVERTAKE")]);
    const stale = mergeSessionIntelligence(current, intelligence(8, [event("old", 8, "PIT_ENTRY")]));
    expect(stale).toBe(current);

    const incoming = intelligence(12, [
      event("same", 11, "OVERTAKE"),
      event("new", 12, "BATTLE_STARTED"),
    ]);
    const merged = mergeSessionIntelligence(current, incoming, 2);
    expect(merged.sequence_number).toBe(12);
    expect(merged.recent_events.map((item) => item.id)).toEqual(["same", "new"]);
    expect(merged.recent_events[0].sequence_number).toBe(11);
  });

  it("ranks the selected driver first and collapses overlapping train cards", () => {
    const ranked = rankBattleCards([
      battle("a", 16, 4, 0.7),
      battle("b", 4, 63, 0.5, "INTENSE"),
      battle("c", 81, 1, 0.8),
    ], 16);

    expect(ranked.map((item) => item.id)).toEqual(["a", "c"]);
    expect(ranked[0].train_size).toBe(3);
  });

  it("filters events without changing chronological order", () => {
    const events = [
      event("battle", 1, "BATTLE_STARTED", [4, 16]),
      event("pit", 2, "PIT_ENTRY", [44]),
      event("control", 3, "RED_FLAG"),
      event("mine", 4, "OVERTAKE", [4, 63]),
    ];

    expect(filterRaceEvents(events, "BATTLES", null).map((item) => item.id)).toEqual([
      "battle",
      "mine",
    ]);
    expect(filterRaceEvents(events, "PITS", null).map((item) => item.id)).toEqual(["pit"]);
    expect(filterRaceEvents(events, "RACE_CONTROL", null).map((item) => item.id)).toEqual([
      "control",
    ]);
    expect(filterRaceEvents(events, "MY_DRIVER", 4).map((item) => item.id)).toEqual([
      "battle",
      "mine",
    ]);
  });

  it("turns structured events into deterministic recent-change wording", () => {
    const overtake = event("pass", 38, "OVERTAKE", [4, 16]);
    overtake.position_after = 4;
    const pit = event("pit", 39, "PIT_ENTRY", [44]);

    expect(describeRecentChanges([overtake, pit], (number) => ({
      4: "Norris",
      16: "Leclerc",
      44: "Hamilton",
    })[number] ?? `Car ${number}`)).toEqual([
      "Norris passed Leclerc for P4.",
      "Hamilton entered the pits.",
    ]);
  });
});
