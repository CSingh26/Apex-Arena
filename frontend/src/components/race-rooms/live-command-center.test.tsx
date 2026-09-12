// SPDX-License-Identifier: AGPL-3.0-only
import { act, render, screen, waitFor, within } from "@testing-library/react";
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

  closed = false;

  close() { this.closed = true; }

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

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, reject, resolve };
}

describe("LiveCommandCenter", () => {
  it("keeps completed captured sessions historical when the provider becomes idle", async () => {
    api.getSessionState.mockResolvedValue({ state: { ...state(53, 1, 0), is_replay: false, status: "finished" } });
    render(<LiveCommandCenter sessionKey="race-1" circuitName="Circuit" eventName="Grand Prix" playbackSequence={0} sessionClock={null} selectedDriver={null} onSelectDriver={vi.fn()} />);
    await screen.findByText("REPLAY");
    act(() => FakeEventSource.instances.at(-1)!.emit("connection_status", JSON.stringify({ connection_state: "WAITING_FOR_SESSION_KEY", current_session_key: null })));
    expect(screen.getByText("REPLAY")).toBeInTheDocument();
    expect(screen.queryByText("RECONNECTING")).not.toBeInTheDocument();
  });

  beforeEach(() => {
    window.localStorage.clear();
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
    expect(screen.getByText("1:22.400")).toBeVisible();
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
      expect.any(AbortSignal),
    ));
  });

  it("rejects recovered API events for another session", async () => {
    api.getSessionState.mockResolvedValue({ state: state(10, 1, 0) });
    api.getSessionEvents
      .mockResolvedValueOnce({ session_key: "race-1", after_sequence_number: 0, count: 100, events: [event("visible", 1)] })
      .mockResolvedValueOnce({
        session_key: "race-1",
        after_sequence_number: 1,
        count: 1,
        events: [{ ...event("foreign", 2), session_key: "race-2" }],
      });
    render(<LiveCommandCenter sessionKey="race-1" circuitName="Circuit" eventName="Grand Prix" playbackSequence={10} sessionClock={null} selectedDriver={null} onSelectDriver={vi.fn()} />);
    const feed = (await screen.findByRole("heading", { name: "Important events" })).closest("section");

    await userEvent.click(await screen.findByRole("button", { name: "Load more events" }));

    expect(feed).not.toBeNull();
    await waitFor(() => expect(within(feed!).getAllByRole("listitem")).toHaveLength(1));
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

  it("refreshes session intelligence after a forward replay seek", async () => {
    api.getSessionState
      .mockResolvedValueOnce({ state: state(1, 1, 0) })
      .mockResolvedValueOnce({ state: state(8, 2, 1.221, 1) });
    const { rerender } = render(<LiveCommandCenter sessionKey="race-1" circuitName="Circuit" eventName="Grand Prix" playbackSequence={1} sessionClock={null} selectedDriver={null} onSelectDriver={vi.fn()} />);

    await screen.findByRole("button", { name: /George Russell, position 1/i });
    rerender(<LiveCommandCenter sessionKey="race-1" circuitName="Circuit" eventName="Grand Prix" playbackSequence={8} sessionClock={null} selectedDriver={null} onSelectDriver={vi.fn()} />);

    await waitFor(() => expect(api.getSessionState).toHaveBeenCalledTimes(2));
    await screen.findByRole("button", { name: /George Russell, position 2/i });
    expect(api.getSessionEvents).toHaveBeenLastCalledWith(
      "race-1",
      { beforeSequenceNumber: 8, limit: 100, minimumImportance: "NORMAL" },
      expect.anything(),
    );
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

  it("does not claim the provider is live from the transport open event", async () => {
    api.getSessionState.mockReturnValue(new Promise(() => undefined));
    render(<LiveCommandCenter sessionKey="race-1" circuitName="Circuit" eventName="Grand Prix" playbackSequence={1} sessionClock={null} selectedDriver={null} onSelectDriver={vi.fn()} />);

    act(() => FakeEventSource.instances[0].emit("open"));

    expect(screen.getByText("RECONNECTING")).toBeVisible();
    act(() => FakeEventSource.instances[0].emit("connection_status", JSON.stringify({
      connection_state: "LIVE",
      current_session_key: "race-1",
    })));
    expect(screen.getByText("LIVE")).toBeVisible();
  });

  it("keeps a newer streamed state when an older snapshot finishes hydrating", async () => {
    const snapshot = deferred<{ state: RaceState }>();
    api.getSessionState.mockReturnValue(snapshot.promise);
    render(<LiveCommandCenter sessionKey="race-1" circuitName="Circuit" eventName="Grand Prix" playbackSequence={1} sessionClock={null} selectedDriver={null} onSelectDriver={vi.fn()} />);

    act(() => FakeEventSource.instances[0].emit("state", JSON.stringify(state(8, 2, 1.221))));
    await screen.findByRole("button", { name: /George Russell, position 2/i });
    await act(async () => snapshot.resolve({ state: state(3, 1, 0) }));

    expect(screen.getByRole("button", { name: /George Russell, position 2/i })).toBeVisible();
    expect(screen.queryByRole("button", { name: /George Russell, position 1/i })).not.toBeInTheDocument();
  });

  it("keeps a healthy stream status when snapshot hydration fails late", async () => {
    const snapshot = deferred<{ state: RaceState }>();
    api.getSessionState.mockReturnValue(snapshot.promise);
    render(<LiveCommandCenter sessionKey="race-1" circuitName="Circuit" eventName="Grand Prix" playbackSequence={1} sessionClock={null} selectedDriver={null} onSelectDriver={vi.fn()} />);

    act(() => {
      FakeEventSource.instances[0].emit("connection_status", JSON.stringify({
        connection_state: "LIVE",
        current_session_key: "race-1",
      }));
      FakeEventSource.instances[0].emit("state", JSON.stringify({ ...state(8, 2, 1.221), is_replay: false }));
    });
    expect(screen.getByRole("status", { name: "Live timing connected" })).toBeVisible();
    await act(async () => snapshot.reject(new Error("snapshot unavailable")));

    expect(screen.getByRole("status", { name: "Live timing connected" })).toBeVisible();
  });

  it("ignores a late snapshot from the previous session", async () => {
    const firstSnapshot = deferred<{ state: RaceState }>();
    api.getSessionState.mockImplementation((sessionKey: string) => sessionKey === "race-1"
      ? firstSnapshot.promise
      : Promise.resolve({ state: { ...state(2, 2, 1.221), session_key: "race-2" } }));
    const view = render(<LiveCommandCenter sessionKey="race-1" circuitName="Circuit" eventName="Grand Prix" playbackSequence={1} sessionClock={null} selectedDriver={null} onSelectDriver={vi.fn()} />);

    view.rerender(<LiveCommandCenter sessionKey="race-2" circuitName="Circuit" eventName="Grand Prix" playbackSequence={2} sessionClock={null} selectedDriver={null} onSelectDriver={vi.fn()} />);
    await screen.findByRole("button", { name: /George Russell, position 2/i });
    await act(async () => firstSnapshot.resolve({ state: state(50, 1, 0) }));

    expect(screen.getByRole("button", { name: /George Russell, position 2/i })).toBeVisible();
    expect(screen.queryByRole("button", { name: /George Russell, position 1/i })).not.toBeInTheDocument();
  });

  it("announces provider state without exposing diagnostic details", async () => {
    api.getSessionState.mockResolvedValue({ state: { ...state(4, 1, 0), is_replay: false } });
    render(<LiveCommandCenter sessionKey="race-1" circuitName="Circuit" eventName="Grand Prix" playbackSequence={4} sessionClock={null} selectedDriver={null} onSelectDriver={vi.fn()} />);

    await screen.findByRole("button", { name: /George Russell, position 1/i });
    expect(screen.getByRole("status", { name: "Reconnecting to live timing" })).toBeVisible();

    act(() => FakeEventSource.instances[0].emit("connection_status", JSON.stringify({
      connection_state: "WAITING_FOR_PROVIDER",
      current_session_key: "race-1",
      degraded_reason: "Bearer secret-provider-token",
      endpoints: { location: { error: "secret-provider-token" } },
    })));
    expect(screen.getByRole("status", { name: "Waiting for timing data" })).toHaveTextContent("WAITING FOR DATA");
    expect(screen.queryByText(/secret-provider-token/)).not.toBeInTheDocument();

    act(() => FakeEventSource.instances[0].emit("connection_status", JSON.stringify({
      connection_state: "LIVE",
      current_session_key: null,
    })));
    expect(screen.getByRole("status", { name: "Waiting for timing data" })).toBeVisible();

    act(() => FakeEventSource.instances[0].emit("connection_status", JSON.stringify({
      connection_state: "PROVIDER_UNAVAILABLE",
      current_session_key: "race-1",
      degraded_reason: "Bearer secret-provider-token",
    })));
    expect(screen.getByRole("status", { name: "Live timing provider unavailable" })).toHaveTextContent("UNAVAILABLE");
    expect(screen.queryByText(/secret-provider-token/)).not.toBeInTheDocument();
  });

  it("describes missing live telemetry as pending instead of permanently absent", async () => {
    api.getSessionState.mockResolvedValue({ state: { ...state(4, 1, 0), is_replay: false } });
    render(<LiveCommandCenter sessionKey="race-1" circuitName="Circuit" eventName="Grand Prix" playbackSequence={4} sessionClock={null} selectedDriver={63} onSelectDriver={vi.fn()} />);
    await screen.findByRole("button", { name: /George Russell, position 1/i });

    await userEvent.click(screen.getByRole("button", { name: "Analyst" }));

    expect(screen.getByText("Car telemetry has not been published for this session yet.")).toBeVisible();
    expect(screen.queryByText("Car telemetry was not recorded for this session.")).not.toBeInTheDocument();
  });

  it("deduplicates recovered events and rejects events for another session", async () => {
    api.getSessionState.mockResolvedValue({ state: state(4, 1, 0) });
    render(<LiveCommandCenter sessionKey="race-1" circuitName="Circuit" eventName="Grand Prix" playbackSequence={4} sessionClock={null} selectedDriver={null} onSelectDriver={vi.fn()} />);
    await screen.findByRole("button", { name: /George Russell, position 1/i });

    act(() => {
      FakeEventSource.instances[0].emit("event", JSON.stringify(event("event-1", 2)));
      FakeEventSource.instances[0].emit("event", JSON.stringify(event("event-1", 2)));
      FakeEventSource.instances[0].emit("event", JSON.stringify({
        ...event("other-session-event", 3),
        session_key: "race-2",
      }));
    });

    const feed = screen.getByRole("heading", { name: "Important events" }).closest("section");
    expect(feed).not.toBeNull();
    expect(within(feed!).getAllByRole("listitem")).toHaveLength(1);
  });

  it("reconnects from the newest accepted state cursor", async () => {
    vi.useFakeTimers();
    try {
      api.getSessionState.mockResolvedValue({ state: state(4, 1, 0) });
      render(<LiveCommandCenter sessionKey="race-1" circuitName="Circuit" eventName="Grand Prix" playbackSequence={4} sessionClock={null} selectedDriver={null} onSelectDriver={vi.fn()} />);
      await act(async () => { await Promise.resolve(); });
      act(() => {
        FakeEventSource.instances[0].emit("state", JSON.stringify(state(9, 2, 1.221)));
        FakeEventSource.instances[0].emit("error");
        vi.advanceTimersByTime(1_000);
      });

      expect(api.sessionStreamUrl).toHaveBeenLastCalledWith("race-1", 9);
      expect(FakeEventSource.instances).toHaveLength(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps the last known state when malformed stream payloads arrive", async () => {
    api.getSessionState.mockResolvedValue({ state: state(1, 1, 0) });
    render(<LiveCommandCenter sessionKey="race-1" circuitName="Circuit" eventName="Grand Prix" playbackSequence={1} sessionClock={null} selectedDriver={null} onSelectDriver={vi.fn()} />);
    await screen.findByRole("button", { name: /George Russell, position 1/i });

    expect(() => act(() => FakeEventSource.instances[0].emit("state", "not json"))).not.toThrow();
    expect(screen.getByRole("button", { name: /George Russell, position 1/i })).toBeVisible();
  });

  it("cleans up the previous stream and resets its cursor and state on session changes", async () => {
    api.getSessionState.mockImplementation((sessionKey: string) => Promise.resolve({
      state: sessionKey === "race-1"
        ? state(10, 1, 0)
        : { ...state(1, 2, 1.221), session_key: "race-2" },
    }));
    const { rerender } = render(<LiveCommandCenter sessionKey="race-1" circuitName="Circuit" eventName="Grand Prix" playbackSequence={10} sessionClock={null} selectedDriver={null} onSelectDriver={vi.fn()} />);
    await screen.findByRole("button", { name: /George Russell, position 1/i });
    const previous = FakeEventSource.instances[0];

    rerender(<LiveCommandCenter sessionKey="race-2" circuitName="Circuit" eventName="Grand Prix" playbackSequence={1} sessionClock={null} selectedDriver={null} onSelectDriver={vi.fn()} />);

    await waitFor(() => expect(api.sessionStreamUrl).toHaveBeenLastCalledWith("race-2", 0));
    expect(previous.closed).toBe(true);
    expect(screen.queryByRole("button", { name: /George Russell, position 1/i })).not.toBeInTheDocument();
    await screen.findByRole("button", { name: /George Russell, position 2/i });
  });

  it("ignores queued event and provider frames from a closed previous-session source", async () => {
    api.getSessionState.mockImplementation((sessionKey: string) => Promise.resolve({
      state: sessionKey === "race-1"
        ? { ...state(10, 1, 0), is_replay: false }
        : { ...state(1, 2, 1.221), session_key: "race-2", is_replay: false },
    }));
    const { rerender } = render(<LiveCommandCenter sessionKey="race-1" circuitName="Circuit" eventName="Grand Prix" playbackSequence={10} sessionClock={null} selectedDriver={null} onSelectDriver={vi.fn()} />);
    await screen.findByRole("button", { name: /George Russell, position 1/i });
    const previous = FakeEventSource.instances[0];

    rerender(<LiveCommandCenter sessionKey="race-2" circuitName="Circuit" eventName="Grand Prix" playbackSequence={1} sessionClock={null} selectedDriver={null} onSelectDriver={vi.fn()} />);
    await screen.findByRole("button", { name: /George Russell, position 2/i });
    const current = FakeEventSource.instances.at(-1)!;
    act(() => current.emit("connection_status", JSON.stringify({
      connection_state: "LIVE",
      current_session_key: "race-2",
    })));

    act(() => {
      previous.emit("event", JSON.stringify(event("closed-source-event", 11)));
      previous.emit("connection_status", JSON.stringify({
        connection_state: "PROVIDER_UNAVAILABLE",
        current_session_key: "race-1",
      }));
    });

    expect(previous.closed).toBe(true);
    expect(screen.getByRole("status", { name: "Live timing connected" })).toHaveTextContent("LIVE");
    const feed = screen.getByRole("heading", { name: "Important events" }).closest("section");
    expect(feed).not.toBeNull();
    expect(within(feed!).queryAllByRole("listitem")).toHaveLength(0);
  });

  it.each([
    ["CONNECTED", "Live timing connected", "LIVE"],
    ["LIVE", "Live timing connected", "LIVE"],
    ["DEGRADED", "Live timing updates degraded", "DEGRADED"],
    ["STALE", "Live timing updates degraded", "DEGRADED"],
    ["ERROR", "Live timing provider unavailable", "UNAVAILABLE"],
    ["DISCONNECTED", "Live timing provider unavailable", "UNAVAILABLE"],
    ["MISSING_CREDENTIALS", "Live timing provider unavailable", "UNAVAILABLE"],
    ["DISABLED", "Live timing provider unavailable", "UNAVAILABLE"],
    ["RECONNECTING", "Reconnecting to live timing", "RECONNECTING"],
    ["CONNECTING", "Reconnecting to live timing", "RECONNECTING"],
    ["AUTHENTICATING", "Reconnecting to live timing", "RECONNECTING"],
    ["PROVIDER_UNAVAILABLE", "Live timing provider unavailable", "UNAVAILABLE"],
    ["WAITING_FOR_PROVIDER", "Waiting for timing data", "WAITING FOR DATA"],
    ["WAITING_FOR_SESSION_KEY", "Waiting for timing data", "WAITING FOR DATA"],
    ["SESSION_COMPLETE", "Historical replay data", "REPLAY"],
  ])("maps provider state %s to its accurate UI state", async (providerState, announcement, label) => {
    api.getSessionState.mockResolvedValue({ state: { ...state(4, 1, 0), is_replay: false } });
    const { unmount } = render(<LiveCommandCenter sessionKey="race-1" circuitName="Circuit" eventName="Grand Prix" playbackSequence={4} sessionClock={null} selectedDriver={null} onSelectDriver={vi.fn()} />);
    await screen.findByRole("button", { name: /George Russell, position 1/i });

    act(() => FakeEventSource.instances.at(-1)!.emit("connection_status", JSON.stringify({
      connection_state: providerState,
      current_session_key: "race-1",
    })));

    expect(screen.getByRole("status", { name: announcement })).toHaveTextContent(label);
    unmount();
  });

  it("updates missing-telemetry copy when room source availability changes", async () => {
    api.getSessionState.mockResolvedValue({ state: { ...state(4, 1, 0), is_replay: false } });
    const props = {
      sessionKey: "race-1",
      circuitName: "Circuit",
      eventName: "Grand Prix",
      playbackSequence: 4,
      sessionClock: null,
      selectedDriver: 63,
      onSelectDriver: vi.fn(),
    } as const;
    const { rerender } = render(<LiveCommandCenter {...props} sourceAvailability="timing_only" />);
    await screen.findByRole("button", { name: /George Russell, position 1/i });
    await userEvent.click(screen.getByRole("button", { name: "Analyst" }));
    expect(screen.getByText("Timing data is available, but this session has no car telemetry.")).toBeVisible();

    rerender(<LiveCommandCenter {...props} sourceAvailability="unavailable" />);
    expect(screen.getByText("Timing and car telemetry are unavailable for this session.")).toBeVisible();

    rerender(<LiveCommandCenter {...props} sourceAvailability="telemetry" />);
    expect(screen.getByText("Car telemetry has not been published for this session yet.")).toBeVisible();
  });

  it("aborts paginated event requests on session replacement and unmount", async () => {
    const firstPage = deferred<{ session_key: string; after_sequence_number: number; count: number; events: NormalizedRaceEvent[] }>();
    const secondPage = deferred<{ session_key: string; after_sequence_number: number; count: number; events: NormalizedRaceEvent[] }>();
    api.getSessionState.mockImplementation((sessionKey: string) => Promise.resolve({
      state: sessionKey === "race-1"
        ? state(10, 1, 0)
        : { ...state(10, 2, 1.221), session_key: "race-2" },
    }));
    api.getSessionEvents
      .mockResolvedValueOnce({ session_key: "race-1", after_sequence_number: 0, count: 100, events: [event("race-1-visible", 1)] })
      .mockReturnValueOnce(firstPage.promise)
      .mockResolvedValueOnce({ session_key: "race-2", after_sequence_number: 0, count: 100, events: [{ ...event("race-2-visible", 1), session_key: "race-2" }] })
      .mockReturnValueOnce(secondPage.promise);
    const view = render(<LiveCommandCenter sessionKey="race-1" circuitName="Circuit" eventName="Grand Prix" playbackSequence={10} sessionClock={null} selectedDriver={null} onSelectDriver={vi.fn()} />);
    await userEvent.click(await screen.findByRole("button", { name: "Load more events" }));
    const firstSignal = api.getSessionEvents.mock.calls[1]?.[2] as AbortSignal | undefined;
    expect(firstSignal).toBeInstanceOf(AbortSignal);

    view.rerender(<LiveCommandCenter sessionKey="race-2" circuitName="Circuit" eventName="Grand Prix" playbackSequence={10} sessionClock={null} selectedDriver={null} onSelectDriver={vi.fn()} />);
    expect(firstSignal?.aborted).toBe(true);
    await act(async () => firstPage.resolve({
      session_key: "race-1",
      after_sequence_number: 1,
      count: 1,
      events: [event("late-race-1-page", 2)],
    }));
    await screen.findByRole("button", { name: /George Russell, position 2/i });
    const feed = screen.getByRole("heading", { name: "Important events" }).closest("section");
    expect(feed).not.toBeNull();
    expect(within(feed!).getAllByRole("listitem")).toHaveLength(1);

    await userEvent.click(await screen.findByRole("button", { name: "Load more events" }));
    const secondSignal = api.getSessionEvents.mock.calls[3]?.[2] as AbortSignal | undefined;
    expect(secondSignal).toBeInstanceOf(AbortSignal);
    view.unmount();
    expect(secondSignal?.aborted).toBe(true);
    await act(async () => secondPage.resolve({
      session_key: "race-2",
      after_sequence_number: 1,
      count: 0,
      events: [],
    }));
  });

  it("schedules one bounded reconnect and cancels it on unmount", () => {
    vi.useFakeTimers();
    try {
      api.getSessionState.mockReturnValue(new Promise(() => undefined));
      const { unmount } = render(<LiveCommandCenter sessionKey="race-1" circuitName="Circuit" eventName="Grand Prix" playbackSequence={1} sessionClock={null} selectedDriver={null} onSelectDriver={vi.fn()} />);
      const first = FakeEventSource.instances[0];

      act(() => {
        first.emit("error");
        first.emit("error");
        vi.advanceTimersByTime(1_000);
      });

      expect(first.closed).toBe(true);
      expect(FakeEventSource.instances).toHaveLength(2);
      unmount();
      act(() => vi.runAllTimers());
      expect(FakeEventSource.instances).toHaveLength(2);
    } finally {
      vi.useRealTimers();
    }
  });
  it("advances live GPS using event time even when room detail contains a paused replay clock", async () => {
    const initial = { ...state(1, 1, 0), is_replay: false, last_updated_at: "2026-09-06T13:30:10Z" };
    api.getSessionState.mockResolvedValue({ state: initial });
    render(<LiveCommandCenter sessionKey="race-1" circuitName="Circuit" eventName="Grand Prix" playbackSequence={0} sessionClock="2026-09-06T13:00:00Z" selectedDriver={null} onSelectDriver={vi.fn()} />);
    await waitFor(() => expect(api.getSessionLocationSamples.mock.calls.some(
      (call) => call[1].since === "2026-09-06T13:30:00.000Z",
    )).toBe(true));
    act(() => FakeEventSource.instances[0].emit("state", JSON.stringify({
      ...initial, sequence_number: 2, last_updated_at: "2026-09-06T13:31:10Z",
    })));
    await waitFor(() => expect(api.getSessionLocationSamples.mock.calls.some(
      (call) => call[1].since === "2026-09-06T13:31:00.000Z",
    )).toBe(true));
  });

});
