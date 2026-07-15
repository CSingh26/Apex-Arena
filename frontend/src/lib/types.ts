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
