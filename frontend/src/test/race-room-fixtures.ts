// SPDX-License-Identifier: AGPL-3.0-only
import type { AgentProfile, RaceRoom, RaceRoomDetailResponse, RoomMessage, RoomPlayback } from "@/lib/types";

const createdAt = "2026-07-17T10:00:00Z";

export const agents: AgentProfile[] = [
  { id: "mira-vale", display_name: "Mira Vale", role: "Race Strategist", short_description: "Reads pit windows, tyre life and undercut threats.", avatar_key: "MV", specialties: ["Pit windows", "Tyre life"], personality_rules: ["Explain trade-offs"], speaking_style: "Calm and methodical.", supported_topics: ["strategy", "pit_stop", "tyres"], active: true, sort_order: 10, ui_accent_key: "copper", created_at: createdAt, updated_at: createdAt },
  { id: "theo-voss", display_name: "Theo Voss", role: "Telemetry Engineer", short_description: "Lives inside lap deltas, sector traces and pace trends.", avatar_key: "TV", specialties: ["Lap deltas", "Consistency"], personality_rules: ["Use sourced numbers"], speaking_style: "Technical and compact.", supported_topics: ["pace", "tyres", "summary"], active: true, sort_order: 20, ui_accent_key: "cyan", created_at: createdAt, updated_at: createdAt },
  { id: "lena-cross", display_name: "Lena Cross", role: "Racecraft Analyst", short_description: "Studies overtakes, defensive driving and track position.", avatar_key: "LC", specialties: ["Overtakes", "Defending"], personality_rules: ["Challenge conclusions"], speaking_style: "Direct and observant.", supported_topics: ["racecraft", "incident"], active: true, sort_order: 30, ui_accent_key: "rose", created_at: createdAt, updated_at: createdAt },
  { id: "arjun-reyes", display_name: "Arjun Reyes", role: "Championship Historian", short_description: "Connects the race to season form and circuit history.", avatar_key: "AR", specialties: ["Season form", "History"], personality_rules: ["Use supplied comparisons"], speaking_style: "Reflective and contextual.", supported_topics: ["championship", "summary"], active: true, sort_order: 40, ui_accent_key: "violet", created_at: createdAt, updated_at: createdAt },
  { id: "nova", display_name: "Nova", role: "Room Host", short_description: "Summarizes major developments and moderates the room.", avatar_key: "N", specialties: ["Moderation", "Evidence quality"], personality_rules: ["Name uncertainty"], speaking_style: "Neutral and concise.", supported_topics: ["summary", "session"], active: true, sort_order: 50, ui_accent_key: "gold", created_at: createdAt, updated_at: createdAt },
];

export const room: RaceRoom = {
  id: "00000000-0000-0000-0000-000000000001", slug: "belgian-grand-prix-race", session_key: "belgian-race-session", season: 2026, round_number: 13,
  race_name: "Belgian Grand Prix", official_name: "Belgian Grand Prix", circuit_name: "Circuit de Spa-Francorchamps", country: "Belgium", country_code: "BE", session_type: "Race",
  scheduled_start: createdAt, actual_start: createdAt, status: "ready", mode: "archived", current_lap: 0, total_laps: 12,
  source_availability: "telemetry", telemetry_quality: "fixture_complete", message_count: 2, agent_count: 5, last_event_at: createdAt,
  created_at: createdAt, updated_at: createdAt, is_featured: true,
};

export const playback: RoomPlayback = { room_id: room.id, current_event_sequence: 0, current_message_sequence: 0, current_lap: 0, playback_speed: 1, is_paused: true, started_at: null, updated_at: createdAt, session_clock: null };

export const detail: RaceRoomDetailResponse = {
  room,
  agents,
  playback,
  circuit: {
    circuit_name: "Apex Validation Circuit",
    records: [
      { label: "Circuit length", value: "5.891 km", detail: null },
      { label: "First Grand Prix", value: "1950", detail: null },
      { label: "Race lap record", value: "1:27.097", detail: "Max Verstappen · 2020" },
    ],
    facts: [
      "The circuit began life as an airfield perimeter road.",
      "Its fastest sequence rewards aerodynamic confidence.",
    ],
    source_url: "https://www.formula1.com/en/racing/2026/great-britain",
  },
  weather: {
    available: true,
    sampled_at: "2026-07-17T10:02:00Z",
    air_temperature_c: 22.5,
    track_temperature_c: 34.1,
    rainfall: false,
    humidity_percent: 71,
    pressure_mbar: 1008.2,
    wind_speed_mps: 3.4,
    wind_direction_degrees: 247,
    source: "OpenF1",
    notice: "Latest weather sample published by OpenF1 for this session.",
  },
  intelligence: {
    session_key: room.session_key ?? "",
    sequence_number: 0,
    current_battles: [],
    recent_events: [],
    qualifying: null,
  },
  data_notice: "Detailed normalized telemetry is available.",
  diagnostics_available: true,
};

export function message(overrides: Partial<RoomMessage> = {}): RoomMessage {
  return {
    id: `00000000-0000-0000-0000-${String(overrides.sequence ?? 1).padStart(12, "0")}`,
    room_id: room.id,
    agent_id: "mira-vale",
    sequence: 1,
    lap_number: 6,
    session_time: 360,
    wall_time: createdAt,
    topic: "strategy",
    message_type: "analysis",
    content: "The 2.41 second stop protects the undercut window.",
    confidence: "high",
    evidence_status: "grounded",
    reply_to_message_id: null,
    trigger_event_id: "10000000-0000-0000-0000-000000000001",
    trigger_snapshot_id: null,
    generated_by: "deterministic",
    model_name: null,
    prompt_version: "rooms-v1",
    created_at: createdAt,
    ...overrides,
  };
}
