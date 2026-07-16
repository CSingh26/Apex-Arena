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

export type RoomStatus = "pending" | "ingesting" | "ready" | "replaying" | "completed" | "failed" | "unavailable";
export type RoomMode = "live" | "replay" | "archived" | "development";
export type SourceAvailability = "telemetry" | "limited_telemetry" | "results_only" | "unavailable";
export type MessageTopic = "strategy" | "pace" | "racecraft" | "incident" | "pit_stop" | "tyres" | "championship" | "summary" | "session";

export type RaceRoom = {
  id: string; slug: string; session_key: string | null; season: number; round_number: number | null;
  race_name: string; official_name: string; circuit_name: string; country: string; session_type: string;
  scheduled_start: string; actual_start: string | null; status: RoomStatus; mode: RoomMode;
  current_lap: number | null; total_laps: number | null; source_availability: SourceAvailability;
  message_count: number; agent_count: number; last_event_at: string | null; created_at: string; updated_at: string;
  is_featured: boolean; is_development: boolean;
};

export type AgentProfile = {
  id: string; name: string; role: string; description: string; avatar_key: string;
  specialties: string[]; personality: string[]; style_rules: string[]; speaking_style: string;
  supported_topics: MessageTopic[]; accent: string; enabled: boolean; sort_order: number;
};

export type RoomMessage = {
  id: string; room_id: string; agent_id: string; sequence: number; lap_number: number | null;
  session_time: number | null; wall_time: string | null; topic: MessageTopic; message_type: string;
  content: string; confidence: "low" | "medium" | "high"; evidence_status: "grounded" | "partial" | "unavailable";
  reply_to_message_id: string | null; trigger_event_id: string | null; generated_by: string; created_at: string;
};

export type MessageEvidence = {
  id: string; message_id: string; evidence_type: string; source_provider: string; source_reference: string;
  metric_name: string | null; metric_value: string | number | null; unit: string | null; context: Record<string, unknown>;
};

export type RoomPlayback = { room_id: string; current_sequence: number; playback_speed: number; is_paused: boolean; updated_at: string };
export type RaceRoomListResponse = { rooms: RaceRoom[]; total: number; limit: number; offset: number };
export type RaceRoomDetailResponse = { room: RaceRoom; agents: AgentProfile[]; playback: RoomPlayback; data_notice: string };
export type RoomMessagesResponse = { messages: RoomMessage[]; next_cursor: number | null };
export type MessageEvidenceResponse = { message_id: string; evidence: MessageEvidence[] };
