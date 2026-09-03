// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SelectedDriverContext } from "./selected-driver-context";
import type { DriverBattleContext, DriverTimingState } from "@/lib/types";

const timing: DriverTimingState[] = [
  {
    driver_number: 16, name: "Charles Leclerc", abbreviation: "LEC", team_name: "Ferrari",
    position: 4, position_change: 0, gap_to_leader: 8.2, interval: 1.4,
    latest_lap: 88.2, best_lap: 87.9, tyre_compound: "HARD", tyre_age_laps: 16,
    pit_stop_count: 1, is_fastest: false, is_personal_best: false, status: "RUNNING",
    battle_context: { driver_number: 16, ahead_driver_number: null, ahead_interval_seconds: null, behind_driver_number: 4, behind_interval_seconds: 0.72, status: "UNDER_PRESSURE", battle_id: "battle" },
  },
  {
    driver_number: 4, name: "Lando Norris", abbreviation: "NOR", team_name: "McLaren",
    position: 5, position_change: 1, gap_to_leader: 8.92, interval: 0.72,
    latest_lap: 87.8, best_lap: 87.5, tyre_compound: "MEDIUM", tyre_age_laps: 8,
    pit_stop_count: 1, is_fastest: false, is_personal_best: true, status: "RUNNING",
    battle_context: { driver_number: 4, ahead_driver_number: 16, ahead_interval_seconds: 0.72, behind_driver_number: null, behind_interval_seconds: null, status: "CLOSING", battle_id: "battle" },
  },
];

describe("SelectedDriverContext", () => {
  it("shows authoritative ahead, behind and battle meaning", () => {
    render(<SelectedDriverContext driverNumber={4} timing={timing} context={timing[1].battle_context} mode="FAN" />);
    expect(screen.getByRole("heading", { name: "Lando Norris" })).toBeVisible();
    expect(screen.getByText("CLOSING")).toBeVisible();
    expect(screen.getByText(/Leclerc · 0.72s/i)).toBeVisible();
  });

  it("omits unsupported neighbors and labels unavailable context", () => {
    const unavailable: DriverBattleContext = {
      driver_number: 99, ahead_driver_number: null, ahead_interval_seconds: null,
      behind_driver_number: null, behind_interval_seconds: null, status: "UNAVAILABLE", battle_id: null,
    };
    render(<SelectedDriverContext driverNumber={99} timing={timing} context={unavailable} mode="ANALYST" />);
    expect(screen.getByText("UNAVAILABLE")).toBeVisible();
    expect(screen.queryByText("Ahead", { selector: "dt" })).not.toBeInTheDocument();
  });
});
