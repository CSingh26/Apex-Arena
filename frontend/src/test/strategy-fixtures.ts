// SPDX-License-Identifier: AGPL-3.0-only
/** Builders mirroring the backend strategy contract for component tests. */
import { STRATEGY_CAPABILITY_KINDS } from "@/lib/strategy-frame";
import type {
  ControlObservation,
  ControlProjection,
  IntelligenceProjection,
  StrategyCapability,
  StrategyEvidence,
  StrategyFrame,
  StrategySituation,
  StrategySituationPayload,
  StrategyBattleContext,
  StrategyBattleProminence,
  TelemetrySample,
  TelemetryWindow,
} from "@/lib/types";

export const SESSION_KEY = "race-1";
const ANALYSIS_TIME = "2026-08-10T13:20:00Z";

export function strategyPayload(
  overrides: Partial<StrategySituationPayload> = {},
): StrategySituationPayload {
  return {
    tyres: [],
    age_offset_laps: null,
    same_reported_team: null,
    plan: "unknown",
    pace: null,
    pit_window: null,
    gap_seconds: null,
    required_gain_seconds: null,
    required_gain_range_seconds: null,
    required_average_gain_seconds: null,
    remaining_laps: null,
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
    ...overrides,
  };
}

export function strategyEvidence(
  overrides: Partial<StrategyEvidence> = {},
): StrategyEvidence {
  return {
    event_id: "11111111-1111-4111-8111-111111111111",
    sequence: 40,
    observed_at: "2026-08-10T13:19:30Z",
    source: "openf1",
    session_key: SESSION_KEY,
    role: "pit",
    family: "pits",
    driver_number: 4,
    lap_number: 23,
    stint_number: 2,
    basis: "observed",
    ...overrides,
  };
}

export function strategySituation(
  overrides: Partial<StrategySituation> = {},
): StrategySituation {
  return {
    situation_id: "22222222-2222-4222-8222-222222222222",
    revision_id: "33333333-3333-4333-8333-333333333333",
    kind: "undercut_condition",
    status: "active",
    transition: "opened",
    superseded_revision_id: null,
    participants: [4, 16],
    source_anchor: "44444444-4444-4444-8444-444444444444",
    source_sequence: 40,
    session_key: SESSION_KEY,
    sequence: 42,
    history_sequence: 41,
    analysis_time: ANALYSIS_TIME,
    semantic_identity: "race-1:v1",
    availability: "available",
    payload: strategyPayload({ required_gain_seconds: 1.284, gap_seconds: 2.031 }),
    evidence_keys: [],
    assumptions: [],
    limitations: [],
    observation_confidence: "observed",
    implication_uncertainty: "unknown",
    ...overrides,
  };
}

export function strategyCapabilities(
  overrides: Record<string, StrategyCapability> = {},
): Record<string, StrategyCapability> {
  const capabilities: Record<string, StrategyCapability> = {};
  for (const kind of STRATEGY_CAPABILITY_KINDS) {
    capabilities[kind] = { availability: "available", reason: "observed_evidence" };
  }
  return { ...capabilities, ...overrides };
}

export function strategyFrame(overrides: Partial<StrategyFrame> = {}): StrategyFrame {
  return {
    session_key: SESSION_KEY,
    sequence: 42,
    history_sequence: 41,
    history_event_id: "55555555-5555-4555-8555-555555555555",
    analysis_time: ANALYSIS_TIME,
    semantic_identity: "race-1:v1",
    clock_basis: "monotonic_consumed_source",
    projection_status: "acknowledged_at_cursor",
    capabilities: strategyCapabilities(),
    situations: [strategySituation()],
    evidence: {},
    situations_truncated: false,
    omitted_situations: 0,
    suppressed_events: 0,
    limitations: [],
    ...overrides,
  };
}

export function intelligenceProjection(
  overrides: Partial<IntelligenceProjection> = {},
): IntelligenceProjection {
  return {
    status: "current",
    completed_through_sequence: 42,
    completed_source_sequence: 42,
    pending_source_sequence: null,
    algorithm_version: "strategy-v1",
    historical_effects_unverified: false,
    failure_code: null,
    ...overrides,
  };
}

function observation(value: string): ControlObservation {
  return { value, evidence: null };
}

export function controlProjection(
  overrides: Partial<ControlProjection> = {},
): ControlProjection {
  return {
    session_key: SESSION_KEY,
    sequence: 42,
    lifecycle: observation("running"),
    neutralization: observation("none"),
    track_flag: observation("green"),
    drs_permission: observation("enabled"),
    sector_flags: {},
    history_truncated: false,
    ...overrides,
  };
}

export function battleProminence(
  overrides: Partial<StrategyBattleProminence> = {},
): StrategyBattleProminence {
  return {
    interval: 24,
    persistence: 9,
    closing: 12,
    lead_position: 6,
    train: 4,
    remaining_distance: 5,
    team_relevance: 0,
    strategy_relevance: 3,
    score: 63,
    basis: "green_timing_pressure",
    ...overrides,
  };
}

/** Defaults mirror the backend's conservative field defaults, not a rich case. */
export function battleContext(
  overrides: Partial<StrategyBattleContext> = {},
): StrategyBattleContext {
  return {
    tyres: [],
    pace: null,
    duration_seconds: null,
    closing: false,
    train_members: [],
    same_reported_team: null,
    remaining_laps: null,
    drs_permission: "unknown",
    within_one_second: null,
    observed_wing_open: null,
    attempt_evidence: "unavailable",
    championship_context: "unavailable",
    prominence: battleProminence(),
    evidence: {},
    limitations: [],
    ...overrides,
  };
}

export function telemetrySample(overrides: Partial<TelemetrySample> = {}): TelemetrySample {
  return {
    sequence: 1,
    observed_at: "2026-08-10T13:19:30Z",
    lap_number: 12,
    speed: 302,
    throttle: 100,
    brake: 0,
    rpm: 11_400,
    gear: 7,
    drs: false,
    ...overrides,
  };
}

export function telemetryWindow(overrides: Partial<TelemetryWindow> = {}): TelemetryWindow {
  return {
    session_key: SESSION_KEY,
    availability: "available",
    reason: null,
    lap_number: 12,
    drivers: [],
    units: { speed: "km/h", rpm: "rpm", throttle: "%", brake: "%", gear: "", drs: "" },
    view_sequence: 42,
    scan_limited: false,
    ...overrides,
  };
}

export function driverName(driverNumber: number): string {
  const names: Record<number, string> = { 4: "Lando Norris", 16: "Charles Leclerc", 63: "George Russell" };
  return names[driverNumber] ?? `Car ${driverNumber}`;
}
