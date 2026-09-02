// SPDX-License-Identifier: AGPL-3.0-only
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CircuitMap, type CircuitMapDriver } from "@/components/race-rooms/circuit-map";
import type { DriverLocationState } from "@/lib/driver-locations";
import type { SessionTrackState } from "@/lib/types";

const BASE = Date.parse("2026-07-19T13:00:00.000Z");

const track: SessionTrackState = {
  session_key: "11334",
  available: true,
  bounds: { min_x: -4330, max_x: 8311, min_y: -15762, max_y: 4537 },
  path: [
    [-4330, -15762],
    [8311, -15762],
    [8311, 4537],
    [-4330, 4537],
    [-4330, -15762],
  ],
  source_driver_number: 1,
  sample_count: 108178,
  first_sample_at: "2026-07-19T12:59:59Z",
  last_sample_at: "2026-07-19T14:32:20Z",
};

const drivers = new Map<number, CircuitMapDriver>([
  [1, { number: 1, code: "VER", name: "Max Verstappen" }],
  [16, { number: 16, code: "LEC", name: "Charles Leclerc" }],
]);

function locationState(driverNumber: number, x: number, y: number, stale = false): DriverLocationState {
  return { driverNumber, x, y, z: 10, timestamp: BASE, stale, interpolated: false };
}

function renderMap(overrides: Partial<Parameters<typeof CircuitMap>[0]> = {}) {
  const onSelectDriver = vi.fn();
  const props = {
    track,
    status: "ready" as const,
    driverNumbers: [1, 16],
    drivers,
    selectedDriver: null,
    onSelectDriver,
    sampleAt: () => [locationState(1, 0, 0), locationState(16, 4000, -6000)],
    currentClockMs: () => BASE,
    ...overrides,
  };
  const view = render(<CircuitMap {...props} />);
  return { ...view, onSelectDriver };
}

async function flushFrames() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 40));
  });
}

describe("CircuitMap", () => {
  beforeEach(() => {
    // jsdom has no layout, but the component only needs frames to fire.
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) =>
      setTimeout(() => callback(performance.now()), 8) as unknown as number,
    );
    vi.stubGlobal("cancelAnimationFrame", (handle: number) => clearTimeout(handle));
  });

  afterEach(() => vi.unstubAllGlobals());

  it("renders a marker for every located driver", async () => {
    renderMap();
    expect(screen.getByRole("button", { name: /Max Verstappen/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Charles Leclerc/ })).toBeInTheDocument();
    expect(screen.getByText("VER")).toBeInTheDocument();
  });

  it("places markers inside the same viewBox as the track", async () => {
    const { container } = renderMap();
    await flushFrames();
    const svg = container.querySelector("svg");
    expect(svg?.getAttribute("viewBox")).toBe("0 0 1000 1000");
    const marker = screen.getByRole("button", { name: /Max Verstappen/ });
    const transform = marker.getAttribute("transform") ?? "";
    const [x, y] = transform.replace(/translate\(|\)/g, "").split(" ").map(Number);
    expect(x).toBeGreaterThanOrEqual(0);
    expect(x).toBeLessThanOrEqual(1000);
    expect(y).toBeGreaterThanOrEqual(0);
    expect(y).toBeLessThanOrEqual(1000);
  });

  it("draws the circuit outline from the provider trace", () => {
    const { container } = renderMap();
    const path = container.querySelector("path");
    expect(path?.getAttribute("d")).toMatch(/^M[\d.]+,[\d.]+ L/);
  });

  it("marks the selected driver and leaves the others plain", () => {
    const { container } = renderMap({ selectedDriver: 16 });
    const selected = screen.getByRole("button", { name: /Charles Leclerc/ });
    const other = screen.getByRole("button", { name: /Max Verstappen/ });
    expect(selected.getAttribute("aria-pressed")).toBe("true");
    expect(other.getAttribute("aria-pressed")).toBe("false");
    expect(selected.getAttribute("class")).not.toBe(other.getAttribute("class"));
    expect(container.querySelectorAll("g[role='button']").length).toBe(2);
  });

  it("emphasizes battle participants while preserving selected-driver precedence", () => {
    const { container } = renderMap({
      selectedDriver: 16,
      battleDrivers: new Set([1, 16]),
    });
    const selected = screen.getByRole("button", { name: /Charles Leclerc/ });
    expect(container.querySelectorAll("circle[class*='battleRing']")).toHaveLength(2);
    expect(selected.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("img")).toHaveAccessibleName(/2 in active battles/i);
    expect(screen.getByText(/Highlighted markers identify drivers/i)).toBeInTheDocument();
  });

  it("reports the clicked driver so the rest of the room follows", async () => {
    const { onSelectDriver } = renderMap();
    await userEvent.click(screen.getByRole("button", { name: /Charles Leclerc/ }));
    expect(onSelectDriver).toHaveBeenCalledWith(16);
  });

  it("selects a driver from the keyboard", async () => {
    const { onSelectDriver } = renderMap();
    screen.getByRole("button", { name: /Max Verstappen/ }).focus();
    await userEvent.keyboard("{Enter}");
    expect(onSelectDriver).toHaveBeenCalledWith(1);
  });

  it("labels a marker by car number when timing metadata is missing", () => {
    renderMap({ driverNumbers: [81], drivers: new Map(), sampleAt: () => [locationState(81, 0, 0)] });
    expect(screen.getByRole("button", { name: /car 81/ })).toBeInTheDocument();
    expect(screen.getByText("81")).toBeInTheDocument();
  });

  it("flags a stale car without removing it", async () => {
    renderMap({ sampleAt: () => [locationState(1, 0, 0, true), locationState(16, 100, 100)] });
    await flushFrames();
    expect(screen.getByRole("button", { name: /Max Verstappen/ }).dataset.stale).toBe("true");
    expect(screen.getByRole("button", { name: /Charles Leclerc/ }).dataset.stale).toBe("false");
  });

  it("renders whatever cars are present rather than requiring a full grid", () => {
    renderMap({ driverNumbers: [1], sampleAt: () => [locationState(1, 0, 0)] });
    expect(screen.getAllByRole("button").length).toBe(1);
  });

  it("shows a loading state instead of claiming there is no data", () => {
    renderMap({ status: "loading", track: null });
    expect(screen.getByText(/Loading circuit map/)).toBeInTheDocument();
    expect(screen.queryByText(/No track positions/)).not.toBeInTheDocument();
  });

  it("distinguishes a provider error from an empty session", () => {
    const { unmount } = renderMap({ status: "error" });
    expect(screen.getByText(/could not be reached/)).toBeInTheDocument();
    unmount();
    renderMap({ status: "no-samples", track: { ...track, sample_count: 0, path: [] } });
    expect(screen.getByText(/No track positions for this session/)).toBeInTheDocument();
  });

  it("falls back to the loaded fixes when geometry is missing", async () => {
    renderMap({ track: { ...track, bounds: null, path: [] } });
    await flushFrames();
    const marker = screen.getByRole("button", { name: /Max Verstappen/ });
    expect(marker.getAttribute("transform")).toMatch(/^translate\(/);
  });

  it("keeps the debug overlay hidden unless it is asked for", async () => {
    const debug = {
      sessionKey: "11334",
      status: "ready" as const,
      loadedSamples: 18_421,
      loadedWindows: 3,
      driversLocated: 2,
      clockIso: "2026-07-19T13:10:00Z",
      lastUpdateAgoMs: null,
      trackPoints: 100,
      trackSampleCount: 108_178,
      bounds: track.bounds,
    };
    const { unmount } = renderMap({ debug });
    expect(screen.queryByLabelText("Location debug")).not.toBeInTheDocument();
    unmount();
    renderMap({ debug, showDebug: true });
    expect(screen.getByLabelText("Location debug")).toBeInTheDocument();
    expect(screen.getByText("11334")).toBeInTheDocument();
    expect(screen.getByText(/X -4330 → 8311/)).toBeInTheDocument();
  });

  it("plots raw fixes when raw debugging is on", async () => {
    const { container } = renderMap({ showRawPoints: true });
    await flushFrames();
    expect(container.querySelectorAll("circle").length).toBeGreaterThan(2);
  });
});
