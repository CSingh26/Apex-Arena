// SPDX-License-Identifier: AGPL-3.0-only
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LiveCommandCenter } from "@/components/race-rooms/live-command-center";
import type { NormalizedRaceEvent, RaceState } from "@/lib/types";

const api = vi.hoisted(() => ({
  getSessionState: vi.fn(),
  sessionStreamUrl: vi.fn((sessionKey: string, sequence: number) => `/stream/${sessionKey}?after=${sequence}`),
  getSessionTrack: vi.fn(),
  getSessionLocationSamples: vi.fn(),
  getSessionEvents: vi.fn(),
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
    current_battles: [],
    recent_events: [],
    qualifying_intelligence: null,
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

function event(id: string, sequence: number): NormalizedRaceEvent {
  return {
    id,
    session_key: "race-1",
    source: "fixture",
    raw_event_id: null,
    event_time: "2026-08-10T00:00:00Z",
    received_at: "2026-08-10T00:00:00Z",
    processed_at: "2026-08-10T00:00:00Z",
    sequence_number: sequence,
    event_type: "PIT_STOP",
    event_origin: "SOURCE_FACT",
    driver_numbers: [63],
    primary_driver_number: 63,
    secondary_driver_number: null,
    position_before: null,
    position_after: null,
    gap_seconds: null,
    interval_seconds: null,
    lap_number: 1,
    importance: 0.5,
    importance_level: "NORMAL",
    confidence: 1,
    confidence_level: "HIGH",
    derivation: null,
    payload: {},
    dedup_key: id,
    is_replay: true,
  };
}

describe("LiveCommandCenter", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    api.getSessionState.mockReset();
    api.sessionStreamUrl.mockClear();
    api.getSessionTrack.mockReset();
    api.getSessionLocationSamples.mockReset();
    api.getSessionEvents.mockReset();
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
    api.getSessionEvents.mockResolvedValue({
      session_key: "race-1", after_sequence_number: 0, count: 0, events: [],
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

  it("bounds the replay event feed to the state sequence", async () => {
    api.getSessionState.mockResolvedValue({ state: state(1, 1, 0) });
    render(<LiveCommandCenter sessionKey="race-1" circuitName="Circuit" eventName="Grand Prix" playbackSequence={1} sessionClock={null} selectedDriver={null} onSelectDriver={vi.fn()} />);

    await waitFor(() => expect(api.getSessionEvents).toHaveBeenCalledWith(
      "race-1",
      { beforeSequenceNumber: 1, limit: 100, minimumImportance: "NORMAL" },
      expect.anything(),
    ));
  });

  it("paginates from the API page instead of server-rendered recent events", async () => {
    api.getSessionState.mockResolvedValue({ state: state(10, 1, 0) });
    api.getSessionEvents
      .mockResolvedValueOnce({ session_key: "race-1", after_sequence_number: 0, count: 100, events: [event("visible", 1)] })
      .mockResolvedValueOnce({ session_key: "race-1", after_sequence_number: 1, count: 0, events: [] });
    render(<LiveCommandCenter
      sessionKey="race-1"
      circuitName="Circuit"
      eventName="Grand Prix"
      playbackSequence={10}
      sessionClock={null}
      selectedDriver={null}
      onSelectDriver={vi.fn()}
      initialIntelligence={{
        session_key: "race-1",
        sequence_number: 10,
        current_battles: [],
        recent_events: [event("recent", 10)],
        qualifying: null,
      }}
    />);

    await userEvent.click(await screen.findByRole("button", { name: "Load more events" }));
    await waitFor(() => expect(api.getSessionEvents).toHaveBeenLastCalledWith(
      "race-1",
      {
        afterSequenceNumber: 1,
        beforeSequenceNumber: 10,
        limit: 100,
        minimumImportance: "NORMAL",
      },
    ));
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

  it("switches presentation modes without refetching or recreating the stream", async () => {
    const initial = state(1, 1, 0);
    initial.drivers["63"].telemetry = {
      speed: 312,
      throttle: 96,
      brake: 0,
      gear: 8,
      drs: true,
    };
    api.getSessionState.mockResolvedValue({ state: initial });
    render(<LiveCommandCenter sessionKey="race-1" circuitName="Circuit" eventName="Grand Prix" playbackSequence={1} sessionClock={null} selectedDriver={63} onSelectDriver={vi.fn()} />);

    await screen.findByRole("button", { name: /George Russell, position 1/i });
    expect(screen.queryByText("SPEED")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Analyst" }));

    expect(screen.getByText("SPEED")).toBeVisible();
    expect(api.getSessionState).toHaveBeenCalledOnce();
    expect(api.getSessionEvents).toHaveBeenCalledOnce();
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(screen.getByRole("button", { name: /George Russell, position 1/i })).toHaveAttribute("aria-pressed", "true");
  });
});
