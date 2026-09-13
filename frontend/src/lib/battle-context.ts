// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Presentation-neutral readers for the Battle Engine's published context.
 *
 * The rules that hold in `strategy-frame.ts` hold here too: nothing is
 * invented, `null` means undetermined and never false or zero, and a field the
 * contract pins to a literal "cannot determine" value is reported as exactly
 * that. Three of those rules are load-bearing for this contract in particular:
 *
 *  1. `attempt_evidence` and `championship_context` are hard-coded
 *     `"unavailable"`. They are listed as undetermined, never rendered as
 *     "no overtake attempts" or "not championship relevant".
 *  2. `observed_wing_open` is the only signal about DRS actually being used.
 *     `within_one_second` is proximity and `drs_permission` is a track state;
 *     neither is ever phrased as a driver using DRS.
 *  3. `strategy_context` being absent means the engine published nothing at
 *     this cursor, so every reader here returns an empty result rather than a
 *     placeholder.
 */
import {
  formatSeconds,
  humaniseCode,
  payloadRows,
  type EvidenceRow,
  type PayloadRow,
} from "@/lib/strategy-frame";
import type {
  StrategyBattleContext,
  StrategyBattleProminence,
  StrategySituationPayload,
  StrategyTyreContext,
} from "@/lib/types";

export type BattleHighlight = { id: string; text: string };

/** Fields the battle contract can never determine, whatever the session shows. */
const ALWAYS_UNDETERMINED: ReadonlyArray<{
  field: "attempt_evidence" | "championship_context";
  label: string;
}> = [
  { field: "attempt_evidence", label: "Overtake attempts" },
  { field: "championship_context", label: "Championship relevance" },
];

const PROMINENCE_LABELS: ReadonlyArray<{
  field: keyof Omit<StrategyBattleProminence, "score" | "basis">;
  label: string;
}> = [
  { field: "interval", label: "Interval" },
  { field: "persistence", label: "Persistence" },
  { field: "closing", label: "Closing" },
  { field: "lead_position", label: "Lead position" },
  { field: "train", label: "Train" },
  { field: "remaining_distance", label: "Remaining distance" },
  { field: "team_relevance", label: "Team relevance" },
  { field: "strategy_relevance", label: "Strategy relevance" },
];

function defaultDriverName(driverNumber: number): string {
  return `Car ${driverNumber}`;
}

/**
 * The battle context shares its measured fields with the situation payload, so
 * they are formatted by the one reader rather than a second, divergent copy.
 * Fields the battle contract does not carry stay null; they are not blanks to
 * be filled in later.
 */
function sharedPayload(context: StrategyBattleContext): StrategySituationPayload {
  return {
    tyres: context.tyres,
    age_offset_laps: null,
    same_reported_team: context.same_reported_team,
    plan: "unknown",
    pace: context.pace,
    pit_window: null,
    gap_seconds: null,
    required_gain_seconds: null,
    required_gain_range_seconds: null,
    required_average_gain_seconds: null,
    remaining_laps: context.remaining_laps,
    pit_anchor: null,
    clean_laps_since_pit: null,
    neutralization: null,
    numeric_saving: null,
    rainfall_before: null,
    rainfall_now: null,
    track_temperature_change: null,
    air_temperature_change: null,
    wind_speed_change: null,
    wind_direction_change: null,
    outcome: "unknown",
    new_tyre_pace: "unknown",
    warmup: "unknown",
    rival_stop_timing: "unknown",
  };
}

/** Seconds as plain words, for prose that a stopwatch format would interrupt. */
export function formatApproximateDuration(value: number | null | undefined): string | null {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return null;
  const total = Math.round(value);
  if (total < 90) return `${total} ${total === 1 ? "second" : "seconds"}`;
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  const minuteWords = `${minutes} ${minutes === 1 ? "minute" : "minutes"}`;
  if (!seconds) return minuteWords;
  return `${minuteWords} ${seconds} ${seconds === 1 ? "second" : "seconds"}`;
}

/** Every measured value the battle context determined, with units. */
export function battleContextRows(context: StrategyBattleContext): PayloadRow[] {
  const rows = payloadRows(sharedPayload(context));
  rows.push({
    label: "Gap trend",
    value: context.closing ? "Closing" : "Not closing at this cursor",
  });
  if (context.duration_seconds != null) {
    rows.push({ label: "Battle duration", value: formatSeconds(context.duration_seconds) });
  }
  if (context.within_one_second != null) {
    rows.push({
      label: "Within one second (proximity)",
      value: context.within_one_second ? "Yes" : "No",
    });
  }
  if (context.observed_wing_open != null) {
    rows.push({
      label: "Rear wing observed open",
      value: context.observed_wing_open ? "Yes" : "Not observed open",
    });
  }
  if (context.drs_permission && context.drs_permission !== "unknown") {
    rows.push({
      label: "DRS permission (track state)",
      value: humaniseCode(context.drs_permission),
    });
  }
  if (context.train_members.length) {
    rows.push({
      label: "Train members",
      value: context.train_members.map((driver) => `Car ${driver}`).join(", "),
    });
  }
  return rows;
}

/** Everything this contract reports as undetermined, named honestly. */
export function battleUndeterminedLabels(context: StrategyBattleContext): string[] {
  const labels = ALWAYS_UNDETERMINED.filter(({ field }) => context[field] === "unavailable").map(
    ({ label }) => label,
  );
  if (!context.drs_permission || context.drs_permission === "unknown") {
    labels.push("DRS permission (track state)");
  }
  if (context.within_one_second == null) labels.push("Within one second (proximity)");
  if (context.observed_wing_open == null) labels.push("Rear wing observed open");
  if (context.same_reported_team == null) labels.push("Same reported team");
  if (context.duration_seconds == null) labels.push("Battle duration");
  if (context.remaining_laps == null) labels.push("Remaining laps");
  if (!context.tyres.length) labels.push("Tyre context");
  if (!context.pace) labels.push("Relative pace");
  return labels;
}

export function prominenceRows(prominence: StrategyBattleProminence): PayloadRow[] {
  const rows = PROMINENCE_LABELS.map(({ field, label }) => ({
    label,
    value: String(prominence[field]),
  }));
  rows.push({ label: "Score", value: String(prominence.score) });
  rows.push({ label: "Basis", value: humaniseCode(prominence.basis) });
  return rows;
}

export function battleEvidenceRows(context: StrategyBattleContext): EvidenceRow[] {
  return Object.entries(context.evidence ?? {})
    .map(([key, evidence]) => ({ key, evidence }))
    .sort((left, right) => left.evidence.sequence - right.evidence.sequence);
}

function tyreFor(
  tyres: readonly StrategyTyreContext[],
  driverNumber: number,
): StrategyTyreContext | undefined {
  return tyres.find((tyre) => tyre.driver_number === driverNumber);
}

/** Compounds arrive shouting (`"MEDIUM"`); prose wants them title-cased. */
function compoundWords(tyre: StrategyTyreContext | undefined): string | null {
  if (!tyre?.compound) return null;
  return humaniseCode(tyre.compound.toLowerCase());
}

/**
 * The tyre-age offset is arithmetic over two published ages, shown only when
 * both were observed. It is never estimated from one side.
 */
function tyreHighlight(
  context: StrategyBattleContext,
  leadNumber: number,
  chasingNumber: number,
  driverName: (driverNumber: number) => string,
): BattleHighlight | null {
  const lead = tyreFor(context.tyres, leadNumber);
  const chasing = tyreFor(context.tyres, chasingNumber);
  const leadCompound = compoundWords(lead);
  const chasingCompound = compoundWords(chasing);
  if (lead?.age_laps != null && chasing?.age_laps != null) {
    const offset = lead.age_laps - chasing.age_laps;
    if (offset === 0) {
      return {
        id: "tyres",
        text: `Both cars are on tyres the same age, so neither has a fresh-rubber advantage.`,
      };
    }
    const laps = Math.abs(offset);
    const fresher = offset > 0 ? driverName(chasingNumber) : driverName(leadNumber);
    const older = offset > 0 ? driverName(leadNumber) : driverName(chasingNumber);
    return {
      id: "tyres",
      text: `${fresher} is on tyres ${laps} ${laps === 1 ? "lap" : "laps"} fresher than ${older}'s.`,
    };
  }
  if (leadCompound && chasingCompound && leadCompound !== chasingCompound) {
    return {
      id: "tyres",
      text: `${driverName(leadNumber)} is on the ${leadCompound} and `
        + `${driverName(chasingNumber)} on the ${chasingCompound}.`,
    };
  }
  return null;
}

export type BattleHighlightInput = {
  context: StrategyBattleContext;
  leadDriverNumber: number;
  chasingDriverNumber: number;
  driverName?: (driverNumber: number) => string;
};

/**
 * Why this battle is worth watching, in plain words.
 *
 * Only determined values produce a line. Proximity is described as proximity,
 * and an open rear wing is described as an observation, never as a driver
 * "attacking with DRS".
 */
export function battleHighlights({
  context,
  leadDriverNumber,
  chasingDriverNumber,
  driverName = defaultDriverName,
}: BattleHighlightInput): BattleHighlight[] {
  const highlights: BattleHighlight[] = [];
  const chaser = driverName(chasingDriverNumber);

  if (context.closing) {
    highlights.push({ id: "closing", text: `${chaser} is taking time out of the car ahead.` });
  }
  if (context.within_one_second === true) {
    highlights.push({
      id: "proximity",
      text: "The two are running inside a second of each other.",
    });
  }
  if (context.observed_wing_open === true) {
    highlights.push({
      id: "wing",
      text: `${chaser}'s rear wing has been observed open on this straight.`,
    });
  }
  if (context.same_reported_team === true) {
    highlights.push({
      id: "team",
      text: "They are team mates, so the pit wall has to referee this one.",
    });
  } else if (context.same_reported_team === false) {
    highlights.push({ id: "team", text: "They are racing for rival teams." });
  }

  const tyres = tyreHighlight(context, leadDriverNumber, chasingDriverNumber, driverName);
  if (tyres) highlights.push(tyres);

  const duration = formatApproximateDuration(context.duration_seconds);
  if (duration) {
    highlights.push({ id: "duration", text: `This fight has been running for ${duration}.` });
  }
  if (context.train_members.length > 2) {
    highlights.push({
      id: "train",
      text: `${context.train_members.length} cars are stacked up in the same train.`,
    });
  }
  if (context.remaining_laps != null) {
    const laps = context.remaining_laps;
    highlights.push({
      id: "remaining",
      text: `${laps} ${laps === 1 ? "lap remains" : "laps remain"} to settle it.`,
    });
  }
  return highlights;
}

/**
 * The plain-words counterpart to `battleUndeterminedLabels`, for Fan mode.
 * Kept deliberately short: a fan needs to know the gap in the data exists,
 * not to read the field list.
 */
export function battleFanCaveat(context: StrategyBattleContext): string | null {
  const unknown: string[] = [];
  if (context.attempt_evidence === "unavailable") unknown.push("whether a move has been attempted");
  if (context.championship_context === "unavailable") {
    unknown.push("what this fight means for the championship");
  }
  if (context.observed_wing_open == null) unknown.push("whether DRS has been used");
  if (!unknown.length) return null;
  const listed = unknown.length === 1
    ? unknown[0]
    : `${unknown.slice(0, -1).join(", ")} or ${unknown[unknown.length - 1]}`;
  return `We cannot tell ${listed}.`;
}
