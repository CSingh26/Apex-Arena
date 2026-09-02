// SPDX-License-Identifier: AGPL-3.0-only
import type {
  BattleIntensity,
  BattleState,
  NormalizedRaceEvent,
  RaceEventFilter,
  SessionIntelligenceState,
} from "./types";

const BATTLE_EVENTS = new Set([
  "BATTLE_STARTED",
  "BATTLE_INTENSIFIED",
  "BATTLE_ENDED",
  "DRS_RANGE_ENTERED",
  "DRS_RANGE_EXITED",
  "OVERTAKE",
]);
const PIT_EVENTS = new Set(["PIT_ENTRY", "PIT_EXIT", "PIT_STOP", "TYRE_CHANGE"]);
const CONTROL_EVENTS = new Set([
  "RACE_CONTROL",
  "YELLOW_FLAG",
  "RED_FLAG",
  "SAFETY_CAR",
  "VIRTUAL_SAFETY_CAR",
  "PENALTY",
  "INVESTIGATION",
]);
const INTENSITY_RANK: Record<BattleIntensity, number> = {
  BUILDING: 1,
  CLOSE: 2,
  INTENSE: 3,
};

export function mergeSessionIntelligence(
  current: SessionIntelligenceState,
  incoming: SessionIntelligenceState,
  historyLimit = 20,
): SessionIntelligenceState {
  if (incoming.sequence_number < current.sequence_number) return current;
  const events = new Map(current.recent_events.map((event) => [event.id, event]));
  for (const event of incoming.recent_events) {
    const previous = events.get(event.id);
    if (!previous || event.sequence_number >= previous.sequence_number) events.set(event.id, event);
  }
  return {
    ...incoming,
    recent_events: [...events.values()]
      .sort((left, right) => left.sequence_number - right.sequence_number)
      .slice(-Math.max(1, historyLimit)),
  };
}

export function rankBattleCards(
  battles: BattleState[],
  selectedDriver: number | null,
  limit = 3,
): BattleState[] {
  const adjacency = new Map<number, Set<number>>();
  for (const battle of battles) {
    const lead = adjacency.get(battle.lead_driver_number) ?? new Set<number>();
    const chaser = adjacency.get(battle.chasing_driver_number) ?? new Set<number>();
    lead.add(battle.chasing_driver_number);
    chaser.add(battle.lead_driver_number);
    adjacency.set(battle.lead_driver_number, lead);
    adjacency.set(battle.chasing_driver_number, chaser);
  }
  const componentSize = (start: number) => {
    const seen = new Set([start]);
    const pending = [start];
    while (pending.length) {
      for (const next of adjacency.get(pending.pop()!) ?? []) {
        if (!seen.has(next)) {
          seen.add(next);
          pending.push(next);
        }
      }
    }
    return seen.size;
  };
  const ordered = [...battles].sort((left, right) => {
    const leftSelected = selectedDriver != null
      && [left.lead_driver_number, left.chasing_driver_number].includes(selectedDriver);
    const rightSelected = selectedDriver != null
      && [right.lead_driver_number, right.chasing_driver_number].includes(selectedDriver);
    return Number(rightSelected) - Number(leftSelected)
      || INTENSITY_RANK[right.intensity] - INTENSITY_RANK[left.intensity]
      || left.interval_seconds - right.interval_seconds
      || left.lead_position - right.lead_position
      || left.id.localeCompare(right.id);
  });
  const used = new Set<number>();
  const ranked: BattleState[] = [];
  for (const battle of ordered) {
    if (used.has(battle.lead_driver_number) || used.has(battle.chasing_driver_number)) continue;
    ranked.push({
      ...battle,
      train_size: Math.max(
        battle.train_size,
        componentSize(battle.lead_driver_number),
      ),
    });
    used.add(battle.lead_driver_number);
    used.add(battle.chasing_driver_number);
    if (ranked.length >= limit) break;
  }
  return ranked;
}

export function filterRaceEvents(
  events: NormalizedRaceEvent[],
  filter: RaceEventFilter,
  selectedDriver: number | null,
): NormalizedRaceEvent[] {
  return [...events]
    .sort((left, right) => left.sequence_number - right.sequence_number)
    .filter((event) => {
      if (filter === "ALL") return true;
      if (filter === "BATTLES") return BATTLE_EVENTS.has(event.event_type);
      if (filter === "PITS") return PIT_EVENTS.has(event.event_type);
      if (filter === "RACE_CONTROL") return CONTROL_EVENTS.has(event.event_type);
      return selectedDriver != null && event.driver_numbers.includes(selectedDriver);
    });
}

export function describeRaceEvent(
  event: NormalizedRaceEvent,
  driverName: (driverNumber: number) => string = (number) => `Car ${number}`,
): string {
  const primary = event.primary_driver_number == null
    ? "A driver"
    : driverName(event.primary_driver_number);
  const secondary = event.secondary_driver_number == null
    ? "another driver"
    : driverName(event.secondary_driver_number);
  switch (event.event_type) {
    case "OVERTAKE":
      return `${primary} passed ${secondary}${event.position_after == null ? "" : ` for P${event.position_after}`}.`;
    case "PIT_ENTRY":
      return `${primary} entered the pits.`;
    case "PIT_EXIT":
      return `${primary} left the pits.`;
    case "PIT_STOP":
      return `${primary} completed a pit stop.`;
    case "BATTLE_STARTED":
      return `${primary} began closing on ${secondary}.`;
    case "BATTLE_INTENSIFIED":
      return `${primary} is now in a more intense fight with ${secondary}.`;
    case "BATTLE_ENDED":
      return `The fight between ${primary} and ${secondary} ended.`;
    case "DRS_RANGE_ENTERED":
      return `${primary} moved within one second of ${secondary}.`;
    case "RED_FLAG":
      return "The session was red flagged.";
    case "SAFETY_CAR":
      return "The safety car was deployed.";
    case "VIRTUAL_SAFETY_CAR":
      return "The virtual safety car was deployed.";
    case "QUALIFYING_CUTOFF_CHANGE":
      return `${primary} changed the qualifying cutoff order.`;
    case "PROVISIONAL_POLE":
      return `${primary} took provisional pole.`;
    case "FASTEST_LAP":
      return `${primary} set the fastest lap.`;
    case "PERSONAL_BEST":
      return `${primary} set a personal best.`;
    default:
      return String(event.payload.message ?? event.event_type.replaceAll("_", " ").toLowerCase());
  }
}

export function describeRecentChanges(
  events: NormalizedRaceEvent[],
  driverName?: (driverNumber: number) => string,
  limit = 5,
): string[] {
  return [...events]
    .sort((left, right) => left.sequence_number - right.sequence_number)
    .slice(-Math.max(1, limit))
    .map((event) => describeRaceEvent(event, driverName));
}
