// SPDX-License-Identifier: AGPL-3.0-only
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { movementLabel, StandingsPage } from "@/components/standings/standings-page";

const metadata = {
  season: 2026,
  generated_at: "2026-08-08T10:00:00Z",
  latest_completed_event: "British Grand Prix",
  races_completed: 12,
  races_remaining: 12,
  source: "OpenF1",
  cached: false,
  cache_age_seconds: 0,
  live: true,
  provisional: true,
  stale: false,
};

const driver = {
  position: 1, driver_id: "driver-one", driver_number: 7, first_name: "Alex", last_name: "Rapid", full_name: "Alex Rapid", acronym: "RAP", country_code: "GBR", headshot_url: "https://media.formula1.com/driver.png", team_id: "velocity", team_name: "Velocity", team_colour: "FF5360", points: 201,
  wins: 4, podiums: 8, poles: 3, fastest_laps: 2, race_starts: 12, classified_finishes: 11, dnfs: 1, dsqs: 0, sprint_starts: 2, sprint_wins: 1, sprint_podiums: 2, sprint_points: 14, best_sprint_finish: 1, average_finish: 3.2, best_finish: 1, average_grid_position: 4.1, best_qualifying_result: 1, q3_appearances: 12, positions_gained_lost: 8, championship_position_change: 2, points_change_from_previous_race: 25, latest_race_finish: 1, latest_race_points: 25, races_completed: 12, points_per_race: 16.8,
};

const constructor = {
  position: 1, constructor_id: "velocity", team_name: "Velocity", team_colour: "FF5360", logo_url: "https://media.formula1.com/velocity.webp", points: 350, wins: 6, podiums: 13, poles: 4, fastest_laps: 3, race_starts: 12, double_podiums: 3, dnfs: 2, sprint_wins: 1, sprint_podiums: 2, average_finish: 4.2, average_points_per_event: 29.2, championship_position_change: 0, points_change_from_previous_race: 40, drivers: [{ driver_id: "driver-one", driver_number: 7, full_name: "Alex Rapid", acronym: "RAP", headshot_url: null, points: 201 }, { driver_id: "driver-two", driver_number: 8, full_name: "Sam Swift", acronym: "SWI", headshot_url: null, points: 149 }], races_completed: 12,
};

function jsonResponse(body: object): Response {
  return { ok: true, json: async () => body } as Response;
}

function mockSuccess() {
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    return Promise.resolve(jsonResponse(url.includes("constructors") ? { metadata, standings: [constructor] } : { metadata, standings: [{ ...driver, position: 2, driver_id: "second", full_name: "Second Driver", points: 180 }, driver] }));
  }));
}

afterEach(() => vi.unstubAllGlobals());

describe("StandingsPage", () => {
  it("sorts drivers, labels live standings, expands details, and switches to constructors", async () => {
    mockSuccess();
    const user = userEvent.setup();
    render(<StandingsPage />);

    expect(document.getElementById("main-content")).toBeInTheDocument();
    expect(screen.getByText("Loading championship standings")).toBeInTheDocument();
    expect(await screen.findByText("Live · Provisional")).toBeVisible();
    const driverRows = screen.getAllByTestId("driver-standing");
    expect(within(driverRows[0]).getByText("Alex Rapid")).toBeVisible();
    expect(within(driverRows[0]).getByLabelText("Up 2 places")).toHaveTextContent("↑ 2");

    await user.click(within(driverRows[0]).getByRole("button"));
    expect(screen.getByText("Race pace")).toBeVisible();
    expect(screen.getByText("Q3 appearances")).toBeVisible();
    expect(screen.getByText("Sprint")).toBeVisible();

    await user.click(screen.getByRole("tab", { name: /Constructors/ }));
    const constructorPanel = screen.getByRole("tabpanel");
    expect(within(constructorPanel).getByText("Velocity")).toBeVisible();
    expect(screen.getAllByAltText("Velocity logo").length).toBeGreaterThan(0);
    await user.click(within(constructorPanel).getByRole("button", { name: /Velocity/ }));
    expect(screen.getByText("Driver contributions")).toBeVisible();
    expect(screen.getByText("Sam Swift")).toBeVisible();
  });

  it("falls back to initials when a headshot fails", async () => {
    mockSuccess();
    render(<StandingsPage />);
    const image = (await screen.findAllByAltText("Alex Rapid headshot"))[0];
    fireEvent.error(image);
    expect(screen.getByLabelText("Alex Rapid portrait unavailable")).toHaveTextContent("RAP");
  });

  it("falls back to a team monogram when a constructor logo fails", async () => {
    mockSuccess();
    render(<StandingsPage />);
    const image = (await screen.findAllByAltText("Velocity logo"))[0];
    fireEvent.error(image);
    expect(screen.getByLabelText("Velocity logo unavailable")).toHaveTextContent("VE");
  });

  it("renders a recoverable unavailable state", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Feed offline")));
    render(<StandingsPage />);
    expect(await screen.findByText("The championship feed is taking a pit stop.")).toBeVisible();
    expect(screen.getByText(/couldn’t reach the championship feed/i)).toBeVisible();
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
  });

  it("renders the empty championship state without fabricated rows", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ metadata: { ...metadata, live: false, provisional: false }, standings: [] })));
    render(<StandingsPage />);
    expect(await screen.findByText("No standings published yet")).toBeVisible();
    expect(screen.getByText("The championship will appear here as soon as official data is available.")).toBeVisible();
  });

  it("keeps available standings visible when one championship feed fails", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => String(input).includes("constructors")
      ? Promise.reject(new Error("Constructor feed offline"))
      : Promise.resolve(jsonResponse({ metadata: { ...metadata, live: false, provisional: false }, standings: [driver] }))));
    render(<StandingsPage />);
    expect(await screen.findByText(/Some championship data is temporarily unavailable/)).toBeVisible();
    expect(screen.getAllByText("Alex Rapid").length).toBeGreaterThan(0);
  });

  it("clearly labels a stale snapshot", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => Promise.resolve(jsonResponse({
      metadata: { ...metadata, live: false, provisional: false, stale: true },
      standings: String(input).includes("constructors") ? [constructor] : [driver],
    }))));
    render(<StandingsPage />);
    expect(await screen.findByText("Last available snapshot")).toBeVisible();
    expect(screen.getByText(/Live data is delayed/)).toBeVisible();
  });

  it("uses accessible, correctly oriented movement labels", () => {
    expect(movementLabel(1)).toBe("Up 1 place");
    expect(movementLabel(-2)).toBe("Down 2 places");
    expect(movementLabel(0)).toBe("No position change");
    expect(movementLabel(null)).toBe("Movement unavailable");
  });

  it("supports arrow-key tab navigation", async () => {
    mockSuccess();
    const user = userEvent.setup();
    render(<StandingsPage />);
    const driversTab = await screen.findByRole("tab", { name: /Drivers/ });
    driversTab.focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: /Constructors/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: /Constructors/ })).toHaveFocus();
  });
});
