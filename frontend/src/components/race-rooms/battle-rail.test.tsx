// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { BattleRail } from "./battle-rail";
import { battleContext } from "@/test/strategy-fixtures";
import type { BattleState, DriverRaceState } from "@/lib/types";

const battle: BattleState = {
  id: "11334:16:4",
  session_key: "11334",
  lead_driver_number: 16,
  chasing_driver_number: 4,
  lead_position: 4,
  chasing_position: 5,
  interval_seconds: 0.72,
  closest_interval_seconds: 0.68,
  interval_history: [0.95, 0.84, 0.72],
  started_at: "2026-07-19T13:20:00Z",
  last_updated_at: "2026-07-19T13:23:00Z",
  trend: "CLOSING",
  intensity: "INTENSE",
  status: "INTENSE",
  within_one_second: true,
  drs_status: "UNAVAILABLE",
  tyre_context: {},
  lap_number: 22,
  train_size: 3,
  resolution_reason: null,
};

const drivers: Record<string, DriverRaceState> = {
  "16": {
    driver_number: 16,
    full_name: "Charles Leclerc",
    broadcast_name: "LECLERC",
    team_name: "Ferrari",
    position: 4,
    position_change: 0,
    gap_to_leader: 8.2,
    interval: 1.4,
    last_lap: {},
    latest_lap_duration: 88.2,
    best_lap_duration: 87.9,
    pit_stops: [],
    stint: { compound: "HARD", lap_start: 7 },
    telemetry: {},
    telemetry_updated_at: null,
    location: {},
    location_updated_at: null,
  },
  "4": {
    driver_number: 4,
    full_name: "Lando Norris",
    broadcast_name: "NORRIS",
    team_name: "McLaren",
    position: 5,
    position_change: 1,
    gap_to_leader: 8.92,
    interval: 0.72,
    last_lap: {},
    latest_lap_duration: 87.8,
    best_lap_duration: 87.5,
    pit_stops: [],
    stint: { compound: "MEDIUM", lap_start: 15 },
    telemetry: {},
    telemetry_updated_at: null,
    location: {},
    location_updated_at: null,
  },
};

describe("BattleRail", () => {
  it("shows concise battle meaning, tyres, proximity and train context", () => {
    render(<BattleRail battles={[battle]} drivers={drivers} currentLap={22} selectedDriver={4} mode="FAN" onSelectDriver={vi.fn()} />);

    expect(screen.getByRole("heading", { name: "Battle for P4" })).toBeVisible();
    expect(screen.getByText("Norris is closing")).toBeVisible();
    expect(screen.getByText("0.72s")).toBeVisible();
    expect(screen.getByText("Within one second")).toBeVisible();
    expect(screen.getByText("3-car train")).toBeVisible();
    expect(screen.getByText(/Medium · 8 laps/i)).toBeVisible();
    expect(screen.getByText(/Hard · 16 laps/i)).toBeVisible();
  });

  it("uses the shared driver selection callback and exposes analyst evidence", async () => {
    const onSelect = vi.fn();
    render(<BattleRail battles={[battle]} drivers={drivers} currentLap={22} selectedDriver={null} mode="ANALYST" onSelectDriver={onSelect} />);

    await userEvent.click(screen.getByRole("button", { name: /select lando norris/i }));
    expect(onSelect).toHaveBeenCalledWith(4);
    await userEvent.click(screen.getByText("Battle evidence"));
    expect(screen.getByText(/Closest 0.68s/i)).toBeVisible();
    expect(screen.getByText(/3 timing samples/i)).toBeVisible();
  });

  it("has a calm empty state when no battle is active", () => {
    render(<BattleRail battles={[]} drivers={drivers} currentLap={22} selectedDriver={null} mode="FAN" onSelectDriver={vi.fn()} />);
    expect(screen.getByText(/No sustained close fight/i)).toBeVisible();
  });

  it("surfaces the engine's context in plain words in fan mode", () => {
    const withContext: BattleState = {
      ...battle,
      strategy_context: battleContext({
        closing: true,
        within_one_second: true,
        same_reported_team: false,
        duration_seconds: 240,
        remaining_laps: 9,
      }),
    };
    render(<BattleRail battles={[withContext]} drivers={drivers} currentLap={22} selectedDriver={null} mode="FAN" onSelectDriver={vi.fn()} />);

    const reasons = screen.getByRole("list", { name: "Why this battle matters" });
    expect(within(reasons).getByText(/taking time out of the car ahead/)).toBeVisible();
    expect(within(reasons).getByText(/inside a second of each other/)).toBeVisible();
    expect(within(reasons).getByText("This fight has been running for 4 minutes.")).toBeVisible();
    expect(screen.getByText(/We cannot tell whether a move has been attempted/)).toBeVisible();
  });

  it("keeps the full published context behind the analyst evidence drawer", async () => {
    const withContext: BattleState = {
      ...battle,
      strategy_context: battleContext({ closing: true, observed_wing_open: true }),
    };
    render(<BattleRail battles={[withContext]} drivers={drivers} currentLap={22} selectedDriver={null} mode="ANALYST" onSelectDriver={vi.fn()} />);

    await userEvent.click(screen.getByText("Battle evidence"));
    expect(screen.getByRole("heading", { name: /^Prominence/ })).toBeVisible();
    expect(screen.getByText("Overtake attempts")).toBeVisible();
    expect(screen.getByText("Championship relevance")).toBeVisible();
    // An observed open wing is the only real DRS-usage signal, and it reads
    // as an observation rather than as the driver attacking with DRS.
    expect(screen.getByText("Rear wing observed open")).toBeVisible();
    expect(screen.getByText("Yes")).toBeVisible();
    expect(screen.queryByText(/DRS attack/i)).not.toBeInTheDocument();
  });

  it("does not show context reasons to fans when the engine published none", () => {
    render(<BattleRail battles={[battle]} drivers={drivers} currentLap={22} selectedDriver={null} mode="FAN" onSelectDriver={vi.fn()} />);

    expect(screen.queryByRole("list", { name: "Why this battle matters" })).not.toBeInTheDocument();
    expect(screen.queryByText(/We cannot tell/)).not.toBeInTheDocument();
    // The intensity-only card is intact rather than blank or errored.
    expect(screen.getByRole("heading", { name: "Battle for P4" })).toBeVisible();
    expect(screen.getByText("0.72s")).toBeVisible();
  });

  it("says the engine published no context rather than showing an empty drawer", async () => {
    render(<BattleRail battles={[battle]} drivers={drivers} currentLap={22} selectedDriver={null} mode="ANALYST" onSelectDriver={vi.fn()} />);

    await userEvent.click(screen.getByText("Battle evidence"));
    expect(screen.getByText(/published no strategy context at this cursor/)).toBeVisible();
    expect(screen.queryByRole("heading", { name: /^Prominence/ })).not.toBeInTheDocument();
  });
});
