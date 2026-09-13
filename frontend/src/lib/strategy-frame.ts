// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Presentation-neutral readers for the deterministic strategy frame.
 *
 * Nothing here invents a value. Every helper either reflects what the backend
 * published or reports, explicitly, that the value was not determined.
 */
import { formatDuration, formatGap, formatPitStop } from "@/lib/timing";
import type {
  StrategyCapability,
  StrategyEvidence,
  StrategyFrame,
  StrategySituation,
  StrategyRelativePaceContext,
  StrategySituationKind,
  StrategySituationPayload,
} from "@/lib/types";

export const STRATEGY_CAPABILITY_KINDS: readonly StrategySituationKind[] = [
  "weather_change",
  "neutralized_pit_context",
  "undercut_condition",
  "overcut_condition",
  "pit_window",
  "extra_stop_consequence",
  "relative_pace",
  "stint_divergence",
];

export const SITUATION_LABELS: Record<StrategySituationKind, string> = {
  stint_divergence: "Tyre plan split",
  relative_pace: "Relative pace",
  pit_window: "Pit window",
  undercut_condition: "Undercut condition",
  overcut_condition: "Overcut condition",
  neutralized_pit_context: "Stop under neutralisation",
  extra_stop_consequence: "Extra stop consequence",
  weather_change: "Weather change",
};

const UNKNOWN_PAYLOAD_LABELS: Array<{ field: keyof StrategySituationPayload; label: string }> = [
  { field: "plan", label: "Team plan" },
  { field: "outcome", label: "Outcome" },
  { field: "new_tyre_pace", label: "Pace on new tyres" },
  { field: "warmup", label: "Tyre warm-up" },
  { field: "rival_stop_timing", label: "Rival stop timing" },
];

const EM_DASH = "—";

export type PayloadRow = { label: string; value: string };
export type EvidenceRow = { key: string; evidence: StrategyEvidence };
export type CapabilityRow = {
  kind: string;
  label: string;
  capability: StrategyCapability;
};

function isKnownKind(kind: string): kind is StrategySituationKind {
  return (STRATEGY_CAPABILITY_KINDS as readonly string[]).includes(kind);
}

/** Turn a backend snake_case reason/limitation code into a readable sentence. */
export function humaniseCode(code: string): string {
  const words = code.trim().replace(/[_\-.]+/g, " ").replace(/\s+/g, " ");
  if (!words) return "Unspecified";
  return `${words.charAt(0).toUpperCase()}${words.slice(1)}`;
}

export function capabilityLabel(kind: string): string {
  return isKnownKind(kind) ? SITUATION_LABELS[kind] : humaniseCode(kind);
}

export function situationLabel(situation: StrategySituation): string {
  return capabilityLabel(situation.kind);
}

/**
 * A seconds amount rendered the way timing is rendered elsewhere in the app.
 * Never milliseconds, never a bare float with a trailing `0.30000000000000004`.
 */
export function formatSeconds(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return EM_DASH;
  const magnitude = Math.abs(value);
  const rendered = magnitude === 0 ? "0.000" : formatDuration(magnitude, 3600);
  if (rendered === EM_DASH) return EM_DASH;
  return value < 0 ? `-${rendered}s` : `${rendered}s`;
}

export function formatSecondsRange(range: readonly [number, number] | null | undefined): string {
  if (!Array.isArray(range) || range.length !== 2) return EM_DASH;
  const low = formatSeconds(range[0]);
  const high = formatSeconds(range[1]);
  if (low === EM_DASH || high === EM_DASH) return EM_DASH;
  return `${low} to ${high}`;
}

function formatSignedUnit(value: number | null | undefined, unit: string): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return EM_DASH;
  const rounded = Number(value.toFixed(1));
  const sign = rounded > 0 ? "+" : "";
  return `${sign}${rounded.toFixed(1)} ${unit}`;
}

function formatLaps(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return EM_DASH;
  const laps = Math.round(Math.abs(value));
  return `${laps} ${laps === 1 ? "lap" : "laps"}`;
}

function tyreSummary(tyre: StrategySituationPayload["tyres"][number]): string {
  const parts = [
    tyre.compound ? humaniseCode(tyre.compound) : "Compound not determined",
    tyre.stint_number == null ? "stint not determined" : `stint ${tyre.stint_number}`,
    tyre.age_laps == null ? "age not determined" : `${formatLaps(tyre.age_laps)} old`,
    `${tyre.stop_count} ${tyre.stop_count === 1 ? "stop" : "stops"} `
      + `(${humaniseCode(tyre.stop_count_basis).toLowerCase()})`,
  ];
  return parts.join(" · ");
}

/** Ordered, unit-bearing rows for everything the payload actually determined. */
export function payloadRows(payload: StrategySituationPayload): PayloadRow[] {
  const rows: PayloadRow[] = [];
  const tyres = Array.isArray(payload.tyres) ? payload.tyres : [];
  for (const tyre of tyres) {
    rows.push({ label: `Car ${tyre.driver_number} tyre`, value: tyreSummary(tyre) });
  }
  if (payload.age_offset_laps != null) {
    const direction = payload.age_offset_laps >= 0 ? "older" : "fresher";
    rows.push({
      label: "Tyre age offset",
      value: `${formatLaps(payload.age_offset_laps)} ${direction}`,
    });
  }
  if (payload.same_reported_team != null) {
    rows.push({ label: "Same reported team", value: payload.same_reported_team ? "Yes" : "No" });
  }
  rows.push(...paceRows(payload));
  rows.push(...pitWindowRows(payload));
  if (payload.gap_seconds != null) {
    rows.push({ label: "Gap", value: formatGap(payload.gap_seconds) });
  }
  if (payload.required_gain_seconds != null) {
    rows.push({ label: "Required gain", value: formatSeconds(payload.required_gain_seconds) });
  }
  if (payload.required_gain_range_seconds) {
    rows.push({
      label: "Required gain range",
      value: formatSecondsRange(payload.required_gain_range_seconds),
    });
  }
  if (payload.required_average_gain_seconds) {
    rows.push({
      label: "Required average gain per lap",
      value: formatSecondsRange(payload.required_average_gain_seconds),
    });
  }
  if (payload.remaining_laps != null) {
    rows.push({ label: "Remaining laps", value: formatLaps(payload.remaining_laps) });
  }
  if (payload.clean_laps_since_pit != null) {
    rows.push({ label: "Clean laps since stop", value: formatLaps(payload.clean_laps_since_pit) });
  }
  if (payload.neutralization) {
    rows.push({ label: "Neutralisation", value: humaniseCode(payload.neutralization) });
  }
  if (payload.pit_anchor) {
    rows.push({ label: "Pit anchor event", value: payload.pit_anchor });
  }
  rows.push(...weatherRows(payload));
  return rows;
}

function paceRows(payload: StrategySituationPayload): PayloadRow[] {
  const pace = payload.pace;
  if (!pace) return [];
  const medianRow = (window: StrategyRelativePaceContext["first"]): PayloadRow => ({
    label: `Car ${window.driver_number} median lap`,
    value: `${formatDuration(window.median_seconds, 3600)} over ${window.sample_laps.length} laps`,
  });
  return [
    medianRow(pace.first),
    medianRow(pace.second),
    { label: "Pace difference", value: formatSeconds(pace.difference_seconds) },
    { label: "Pace difference range", value: formatSecondsRange(pace.range_seconds) },
    { label: "Shared conditions", value: humaniseCode(pace.shared_conditions) },
  ];
}

function pitWindowRows(payload: StrategySituationPayload): PayloadRow[] {
  const window = payload.pit_window;
  if (!window) return [];
  const rows: PayloadRow[] = [
    { label: "Pit-lane loss", value: formatPitStop(window.loss_seconds) },
    { label: "Pit-lane loss range", value: formatSecondsRange(window.loss_range_seconds) },
    { label: "Projected gap after stop", value: formatSecondsRange(window.projected_gap_seconds) },
    { label: "Timing basis", value: humaniseCode(window.timing_basis) },
  ];
  if (window.rank_range) {
    rows.push({
      label: "Projected rank range",
      value: `P${window.rank_range[0]} to P${window.rank_range[1]}`,
    });
  }
  rows.push({
    label: "Traffic in the window",
    value: window.traffic.length
      ? window.traffic.map((driver) => `Car ${driver}`).join(", ")
      : "None observed",
  });
  return rows;
}

function weatherRows(payload: StrategySituationPayload): PayloadRow[] {
  const rows: PayloadRow[] = [];
  if (payload.rainfall_before != null || payload.rainfall_now != null) {
    const before = payload.rainfall_before == null ? "not determined" : payload.rainfall_before ? "raining" : "dry";
    const now = payload.rainfall_now == null ? "not determined" : payload.rainfall_now ? "raining" : "dry";
    rows.push({ label: "Rainfall", value: `${before} → ${now}` });
  }
  if (payload.track_temperature_change != null) {
    rows.push({
      label: "Track temperature change",
      value: formatSignedUnit(payload.track_temperature_change, "°C"),
    });
  }
  if (payload.air_temperature_change != null) {
    rows.push({
      label: "Air temperature change",
      value: formatSignedUnit(payload.air_temperature_change, "°C"),
    });
  }
  if (payload.wind_speed_change != null) {
    rows.push({ label: "Wind speed change", value: formatSignedUnit(payload.wind_speed_change, "m/s") });
  }
  if (payload.wind_direction_change != null) {
    rows.push({
      label: "Wind direction change",
      value: formatSignedUnit(payload.wind_direction_change, "°"),
    });
  }
  return rows;
}

/** Fields the contract pins to the literal `"unknown"`: the system cannot decide them. */
export function undeterminedLabels(payload: StrategySituationPayload): string[] {
  return UNKNOWN_PAYLOAD_LABELS.filter(({ field }) => payload[field] === "unknown").map(
    ({ label }) => label,
  );
}

/** Evidence rows for a situation, plus the keys the frame failed to close over. */
export function evidenceFor(
  frame: StrategyFrame,
  situation: StrategySituation,
): { rows: EvidenceRow[]; missingKeys: string[] } {
  const rows: EvidenceRow[] = [];
  const missingKeys: string[] = [];
  for (const key of situation.evidence_keys) {
    const evidence = frame.evidence[key];
    if (evidence) rows.push({ key, evidence });
    else missingKeys.push(key);
  }
  rows.sort((left, right) => left.evidence.sequence - right.evidence.sequence);
  return { rows, missingKeys };
}

/** The eight contract capabilities in product order, plus anything extra the backend sent. */
export function capabilityRows(frame: StrategyFrame): CapabilityRow[] {
  const capabilities = frame.capabilities ?? {};
  const known = STRATEGY_CAPABILITY_KINDS.map((kind) => ({
    kind: kind as string,
    label: capabilityLabel(kind),
    capability: capabilities[kind] ?? { availability: "unavailable" as const, reason: "missing_capability" },
  }));
  const extra = Object.keys(capabilities)
    .filter((kind) => !isKnownKind(kind))
    .sort()
    .map((kind) => ({ kind, label: capabilityLabel(kind), capability: capabilities[kind] }));
  return [...known, ...extra];
}

const FAN_PRIORITY: Record<StrategySituationKind, number> = {
  weather_change: 0,
  neutralized_pit_context: 1,
  undercut_condition: 2,
  overcut_condition: 3,
  pit_window: 4,
  extra_stop_consequence: 5,
  relative_pace: 6,
  stint_divergence: 7,
};

/**
 * Active situations first, then withdrawals (which still have to be told), each
 * group ordered by product relevance and then by recency of the source cursor.
 */
export function rankSituations(situations: readonly StrategySituation[]): StrategySituation[] {
  return [...situations].sort((left, right) => {
    const withdrawn = Number(left.status === "withdrawn") - Number(right.status === "withdrawn");
    if (withdrawn !== 0) return withdrawn;
    const priority = FAN_PRIORITY[left.kind] - FAN_PRIORITY[right.kind];
    if (priority !== 0) return priority;
    return right.sequence - left.sequence;
  });
}

export function isHedged(situation: StrategySituation): boolean {
  return situation.observation_confidence === "qualified" || situation.availability !== "available";
}

export function formatAnalysisTime(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toISOString().replace("T", " ").replace(/\.\d+Z$/, "Z");
}
