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
  id: "00000000-0000-0000-0000-000000000001", slug: "day3-validation-room", session_key: "day3-validation", season: 2026, round_number: 12,
  race_name: "Day 3 Validation Grand Prix", official_name: "ApexArena Day 3 Validation Grand Prix", circuit_name: "Apex Validation Circuit", country: "Test Territory", country_code: "TT", session_type: "Race",
  scheduled_start: createdAt, actual_start: createdAt, status: "ready", mode: "development", current_lap: 0, total_laps: 12,
  source_availability: "telemetry", telemetry_quality: "fixture_complete", message_count: 2, agent_count: 5, last_event_at: createdAt,
  created_at: createdAt, updated_at: createdAt, is_featured: true, is_development: true,
};

export const playback: RoomPlayback = { room_id: room.id, current_event_sequence: 0, current_message_sequence: 0, current_lap: 0, playback_speed: 1, is_paused: true, started_at: null, updated_at: createdAt };

export const detail: RaceRoomDetailResponse = { room, agents, playback, data_notice: "Detailed normalized telemetry is available.", diagnostics_available: true };

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
