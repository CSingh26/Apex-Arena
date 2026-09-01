// SPDX-License-Identifier: AGPL-3.0-only
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LiveCommandCenter } from "@/components/race-rooms/live-command-center";
import type { RaceState } from "@/lib/types";

const api = vi.hoisted(() => ({
  getSessionState: vi.fn(),
  sessionStreamUrl: vi.fn((sessionKey: string, sequence: number) => `/stream/${sessionKey}?after=${sequence}`),
  getSessionTrack: vi.fn(),
  getSessionLocationSamples: vi.fn(),
}));

vi.mock("@/lib/api", () => api);

class FakeEventSource {
  static instances: FakeEventSource[] = [];

  readonly listeners = new Map<string, EventListener[]>();

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: EventListener) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  close() {}

  emit(type: string, data?: string) {
    const event = new MessageEvent(type, { data });
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
}

function state(sequence: number, position: number, gap: number, pits = 0): RaceState {
  return {
    session_key: "race-1",
    session_type: "RACE",
    current_phase: "RACE",
    status: "running",
    current_lap: sequence,
    sequence_number: sequence,
    is_replay: true,
    race_control_state: sequence === 2 ? { event_type: "SAFETY_CAR" } : {},
    weather: {},
    last_updated_at: "2026-08-10T00:00:00Z",
    drivers: {
      "63": {
        driver_number: 63,
        full_name: "George Russell",
        broadcast_name: "RUSSELL",
        team_name: "Mercedes",
        position,
        position_change: position === 1 ? null : -1,
        gap_to_leader: gap,
        interval: gap,
        last_lap: {},
        latest_lap_duration: 82.4,
        best_lap_duration: 81.9,
        pit_stops: Array.from({ length: pits }, () => ({})),
        stint: { compound: "HARD", lap_start: 1 },
        telemetry: {},
        telemetry_updated_at: null,
        location: {},
        location_updated_at: null,
      },
    },
  };
}

describe("LiveCommandCenter", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    api.getSessionState.mockReset();
    api.sessionStreamUrl.mockClear();
    api.getSessionTrack.mockReset();
    api.getSessionLocationSamples.mockReset();
    api.getSessionTrack.mockResolvedValue({
      track: {
        session_key: "race-1",
        available: false,
        bounds: null,
        path: [],
        source_driver_number: null,
        sample_count: 0,
        first_sample_at: null,
        last_sample_at: null,
      },
    });
    api.getSessionLocationSamples.mockResolvedValue({
      locations: { session_key: "race-1", count: 0, drivers: [], since: null, until: null, samples: [] },
    });
    vi.stubGlobal("EventSource", FakeEventSource);
  });

  afterEach(() => vi.unstubAllGlobals());

  it("updates the tower, selected-driver gap, and race-control status from replay state events", async () => {
    api.getSessionState.mockResolvedValue({ state: state(1, 1, 0) });
    render(<LiveCommandCenter sessionKey="race-1" circuitName="Circuit" eventName="Grand Prix" playbackSequence={1} sessionClock={null} selectedDriver={null} onSelectDriver={vi.fn()} />);

    await screen.findByRole("button", { name: /George Russell, position 1/i });
    act(() => FakeEventSource.instances[0].emit("state", JSON.stringify(state(2, 2, 1.221, 1))));

    await screen.findByRole("button", { name: /George Russell, position 2/i });
    expect(screen.getByText("SAFETY CAR")).toBeVisible();
    expect(screen.getAllByText("+1.221")).toHaveLength(2);
    expect(screen.getByText("1", { selector: "dd" })).toBeVisible();
  });

  it("accepts lower sequence states after replay restart or backward seek", async () => {
    api.getSessionState
      .mockResolvedValueOnce({ state: state(10, 1, 0) })
      .mockResolvedValueOnce({ state: state(1, 2, 1.221) });
    const { rerender } = render(<LiveCommandCenter sessionKey="race-1" circuitName="Circuit" eventName="Grand Prix" playbackSequence={10} sessionClock={null} selectedDriver={null} onSelectDriver={vi.fn()} />);

    await screen.findByRole("button", { name: /George Russell, position 1/i });
    rerender(<LiveCommandCenter sessionKey="race-1" circuitName="Circuit" eventName="Grand Prix" playbackSequence={0} sessionClock={null} selectedDriver={null} onSelectDriver={vi.fn()} />);

    await waitFor(() => expect(api.getSessionState).toHaveBeenCalledTimes(2));
    await screen.findByRole("button", { name: /George Russell, position 2/i });
    expect(api.sessionStreamUrl).toHaveBeenLastCalledWith("race-1", 0);
  });
});
