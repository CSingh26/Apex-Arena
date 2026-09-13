// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TelemetryComparison } from "@/components/race-rooms/telemetry-comparison";
import { telemetrySample, telemetryWindow } from "@/test/strategy-fixtures";
import type { DriverTelemetrySeries, TelemetryWindow } from "@/lib/types";

const api = vi.hoisted(() => ({
  getSessionTelemetryHistory: vi.fn(),
  ApiError: class ApiError extends Error {
    constructor(message: string, readonly status = 500) {
      super(message);
      this.name = "ApiError";
    }
  },
}));

vi.mock("@/lib/api", () => api);

const DRIVERS = [
  { driverNumber: 4, name: "Lando Norris" },
  { driverNumber: 16, name: "Charles Leclerc" },
  { driverNumber: 63, name: "George Russell" },
];

const CLOCK = (offsetSeconds: number) =>
  new Date(Date.parse("2026-08-10T13:19:30Z") + offsetSeconds * 1000).toISOString();

function driverSeries(
  driverNumber: number,
  channels: string[],
  samples: DriverTelemetrySeries["samples"],
  truncated = false,
): DriverTelemetrySeries {
  return { driver_number: driverNumber, channels, samples, samples_truncated: truncated };
}

function populatedSeries(driverNumber: number): DriverTelemetrySeries {
  return driverSeries(driverNumber, ["speed", "rpm"], [
    telemetrySample({ sequence: 1, observed_at: CLOCK(0), speed: 280, rpm: 10_800 }),
    telemetrySample({ sequence: 2, observed_at: CLOCK(1), speed: 305, rpm: 11_900 }),
  ]);
}

function renderPanel() {
  return render(
    <TelemetryComparison
      sessionKey="race-1"
      drivers={DRIVERS}
      currentLap={12}
      viewSequence={42}
    />,
  );
}

async function compare(drivers: string[] = ["Lando Norris"]) {
  for (const driver of drivers) {
    await userEvent.click(screen.getByRole("checkbox", { name: driver }));
  }
  await userEvent.click(screen.getByRole("button", { name: /compare telemetry/i }));
}

function resolveWith(window: TelemetryWindow) {
  api.getSessionTelemetryHistory.mockResolvedValue(window);
}

describe("TelemetryComparison", () => {
  beforeEach(() => {
    api.getSessionTelemetryHistory.mockReset();
  });

  it("reads nothing until the reader explicitly asks for a comparison", async () => {
    resolveWith(telemetryWindow());
    renderPanel();

    expect(api.getSessionTelemetryHistory).not.toHaveBeenCalled();
    expect(screen.getByText(/never on the live timing tick/)).toBeVisible();
    await userEvent.click(screen.getByRole("checkbox", { name: "Lando Norris" }));
    // Choosing a car is not the request; submitting it is.
    expect(api.getSessionTelemetryHistory).not.toHaveBeenCalled();
  });

  it("sends the selection, lap and view cursor on submit", async () => {
    resolveWith(telemetryWindow({ drivers: [populatedSeries(4)] }));
    renderPanel();

    await userEvent.type(screen.getByRole("spinbutton", { name: "Lap" }), "12");
    await compare();

    await waitFor(() => expect(api.getSessionTelemetryHistory).toHaveBeenCalledOnce());
    expect(api.getSessionTelemetryHistory).toHaveBeenCalledWith(
      "race-1",
      [4],
      { lapNumber: 12, cursor: 42 },
      expect.any(AbortSignal),
    );
  });

  it("plots the available channels for two cars with published units", async () => {
    resolveWith(
      telemetryWindow({ drivers: [populatedSeries(4), populatedSeries(16)] }),
    );
    renderPanel();
    await compare(["Lando Norris", "Charles Leclerc"]);

    expect(await screen.findByRole("img", { name: /^Speed trace/ })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /^Engine RPM trace/ })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Speed" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Engine RPM" })).toBeVisible();
    // Units come from the published `units` map, not from a hard-coded guess.
    expect(screen.getByText("km/h")).toBeVisible();
    expect(screen.getByText("rpm")).toBeVisible();
    expect(screen.getByText(/Lando Norris: 280 km\/h to 305 km\/h/)).toBeVisible();
    expect(screen.getByText(/Charles Leclerc: 10,800 rpm to 11,900 rpm/)).toBeVisible();
  });

  it("refuses a third car rather than sending a selection the backend rejects", async () => {
    resolveWith(telemetryWindow());
    renderPanel();

    await userEvent.click(screen.getByRole("checkbox", { name: "Lando Norris" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Charles Leclerc" }));

    expect(screen.getByRole("checkbox", { name: "George Russell" })).toBeDisabled();
  });

  it("names a car that published no channel instead of drawing it as zero", async () => {
    resolveWith(
      telemetryWindow({
        availability: "partial",
        reason: "telemetry_missing_for_some_drivers",
        drivers: [
          driverSeries(4, ["speed", "brake"], [
            telemetrySample({ sequence: 1, observed_at: CLOCK(0), speed: 280, brake: 0 }),
            telemetrySample({ sequence: 2, observed_at: CLOCK(1), speed: 300, brake: 90 }),
          ]),
          driverSeries(16, ["speed"], [
            telemetrySample({ sequence: 1, observed_at: CLOCK(0), speed: 275, brake: null }),
          ]),
        ],
      }),
    );
    renderPanel();
    await compare(["Lando Norris", "Charles Leclerc"]);

    expect(await screen.findByText(/Telemetry partially available/)).toBeVisible();
    expect(
      screen.getByText(/Charles Leclerc published no brake channel for this window/),
    ).toBeVisible();
    expect(screen.getByText(/A missing channel is not a reading of zero/)).toBeVisible();
    const brake = screen.getByRole("img", { name: /^Brake trace/ });
    expect(brake.getAttribute("aria-label")).toContain("Lando Norris");
    expect(brake.getAttribute("aria-label")).not.toContain("Charles Leclerc");
  });

  it("says so when a published channel landed no value in the window", async () => {
    resolveWith(
      telemetryWindow({
        drivers: [
          driverSeries(4, ["speed", "brake"], [
            telemetrySample({ sequence: 1, observed_at: CLOCK(0), speed: 280, brake: null }),
            telemetrySample({ sequence: 2, observed_at: CLOCK(1), speed: 300, brake: null }),
          ]),
        ],
      }),
    );
    renderPanel();
    await compare();

    expect(
      await screen.findByText(/published the brake channel but no value landed inside this window/),
    ).toBeVisible();
    expect(screen.queryByRole("img", { name: /^Brake trace/ })).not.toBeInTheDocument();
    expect(screen.getByRole("img", { name: /^Speed trace/ })).toBeInTheDocument();
  });

  it("gives each unavailability reason its own explanation", async () => {
    resolveWith(
      telemetryWindow({
        availability: "unavailable",
        reason: "no_telemetry_for_lap",
        drivers: [driverSeries(4, [], [])],
      }),
    );
    renderPanel();
    await compare();

    expect(await screen.findByText(/Telemetry unavailable/)).toBeVisible();
    expect(screen.getByText(/No car telemetry was retained for the selected lap/)).toBeVisible();
    expect(screen.getByText(/No telemetry came back for Lando Norris/)).toBeVisible();
    expect(screen.getByText(/there is nothing to plot/)).toBeVisible();
  });

  it("distinguishes a retention gap from a scan ceiling", async () => {
    resolveWith(
      telemetryWindow({
        availability: "partial",
        reason: "scan_limit_reached",
        scan_limited: true,
        drivers: [populatedSeries(4)],
      }),
    );
    renderPanel();
    await compare();

    expect(await screen.findByText(/hit its scan ceiling/)).toBeVisible();
    expect(screen.queryByText(/No car telemetry was retained/)).not.toBeInTheDocument();
  });

  it("says a series was cut short by the sample ceiling", async () => {
    resolveWith(
      telemetryWindow({
        drivers: [driverSeries(4, ["speed"], [
          telemetrySample({ sequence: 1, observed_at: CLOCK(0), speed: 280 }),
          telemetrySample({ sequence: 2, observed_at: CLOCK(1), speed: 300 }),
        ], true)],
      }),
    );
    renderPanel();
    await compare();

    expect(await screen.findByText(/hit the per-driver sample ceiling/)).toBeVisible();
  });

  it("aborts a superseded read rather than letting it land over a newer one", async () => {
    const signals: AbortSignal[] = [];
    api.getSessionTelemetryHistory.mockImplementation(
      (_key: string, _drivers: number[], _options: unknown, signal: AbortSignal) => {
        signals.push(signal);
        return signals.length === 1
          ? new Promise(() => undefined)
          : Promise.resolve(telemetryWindow({ drivers: [populatedSeries(4)] }));
      },
    );
    renderPanel();

    await compare();
    await userEvent.click(screen.getByRole("button", { name: /compare telemetry/i }));

    await waitFor(() => expect(signals).toHaveLength(2));
    expect(signals[0].aborted).toBe(true);
    expect(signals[1].aborted).toBe(false);
  });

  it("reports a failed read instead of showing an empty chart", async () => {
    api.getSessionTelemetryHistory.mockRejectedValue(
      new api.ApiError("Telemetry is temporarily unavailable; retry shortly", 503),
    );
    renderPanel();
    await compare();

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText(/temporarily unavailable/)).toBeVisible();
  });
});
