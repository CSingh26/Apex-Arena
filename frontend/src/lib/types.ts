// SPDX-License-Identifier: AGPL-3.0-only
export type ComponentStatus = {
  status: string;
  detail: string | null;
};

export type HealthResponse = {
  status: "healthy" | "degraded";
  checked_at: string;
  app: ComponentStatus & {
    name: string;
    environment: string;
    season: number;
  };
  database: ComponentStatus;
  redis: ComponentStatus;
  openf1_rest: ComponentStatus;
  openf1_live: ComponentStatus;
  jolpica: ComponentStatus;
  ai: ComponentStatus;
};

export type MeetingLifecycleStatus = "completed" | "upcoming" | "live";

export type RaceMeeting = {
  id: string;
  season_year: number;
  round_number: number;
  race_name: string;
  circuit_id: string;
  circuit_name: string;
  locality: string;
  country: string;
  race_date: string;
  race_start: string;
  status: MeetingLifecycleStatus;
  is_target: boolean;
  source_url: string | null;
};

export type SeasonCalendarSummary = {
  season_year: number;
  source: string;
  generated_at: string;
  total_races: number;
  completed_races: number;
  upcoming_races: number;
  live_races: number;
  target_found: boolean;
  target_grand_prix: string;
  target_circuit: string;
  races: RaceMeeting[];
};

export type LiveStatus = {
  live_mode_enabled: boolean;
  credentials_present: boolean;
  auth_available: boolean;
  token_available: boolean;
  token_expires_in_seconds: number | null;
  connection_state: string;
  last_event_at: string | null;
  reconnect_attempts: number;
  current_session_key: string | null;
  degraded_reason: string | null;
};

export type IngestionRun = {
  id: string;
  provider: string;
  session_key: string | null;
  status: string;
  started_at: string;
  ended_at: string | null;
  last_event_at: string | null;
  last_error: string | null;
  metadata: Record<string, unknown>;
  raw_inserted: number;
  duplicates: number;
  normalized_inserted: number;
};

export type EngineStatus = {
  status: string;
  generated_at: string;
  database: ComponentStatus;
  redis: ComponentStatus;
  current_session_key: string | null;
  raw_event_count: number;
  normalized_event_count: number;
  snapshot_count: number;
  latest_sequence_number: number;
  ordering_buffer_pending: number;
  historical_ingestion_enabled: boolean;
  debug_ingestion_enabled: boolean;
  live: LiveStatus;
  latest_ingestion: IngestionRun | null;
};

export type NormalizedRaceEvent = {
  id: string;
  session_key: string;
  source: string;
  event_time: string;
  processed_at: string;
  sequence_number: number;
  event_type: string;
  driver_numbers: number[];
  lap_number: number | null;
  payload: Record<string, unknown>;
  is_replay: boolean;
};

export type DriverRaceState = {
  position: number | null;
  gap_to_leader: number | string | null;
  interval: number | string | null;
  last_lap: Record<string, unknown>;
  pit_stops: Record<string, unknown>[];
  stint: Record<string, unknown>;
};

export type RaceState = {
  session_key: string;
  status: string;
  current_lap: number | null;
  drivers: Record<string, DriverRaceState>;
  race_control_state: Record<string, unknown>;
  weather: Record<string, unknown>;
  last_updated_at: string | null;
  sequence_number: number;
  is_replay: boolean;
};

export type SessionEventsResponse = {
  session_key: string;
  after_sequence_number: number;
  count: number;
  events: NormalizedRaceEvent[];
};

export type SessionStateResponse = {
  state: RaceState;
};
