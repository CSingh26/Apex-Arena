// SPDX-License-Identifier: AGPL-3.0-only
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RoomExperience } from "@/components/race-rooms/room-experience";
import { detail, message } from "@/test/race-room-fixtures";
import type { RaceRoomDetailResponse, RoomPlayback } from "@/lib/types";

const api = vi.hoisted(() => ({
  getRaceRoom: vi.fn(),
  getRoomMessages: vi.fn(),
  roomStreamUrl: vi.fn(() => "/stream"),
  startRoomReplay: vi.fn(),
  updateRoomPlayback: vi.fn(),
  verifyReplayOperator: vi.fn(),
}));

vi.mock("@/lib/api", () => api);
vi.mock("@/components/race-rooms/live-command-center", () => ({
  LiveCommandCenter: ({
    sessionKey,
    locationDataMode,
  }: {
    sessionKey: string | null;
    locationDataMode?: string;
  }) => (
    <div>
      <span data-testid="session-key">{sessionKey ?? "waiting"}</span>
      <span data-testid="location-data-mode">{locationDataMode ?? "missing"}</span>
    </div>
  ),
}));
vi.mock("@/components/race-rooms/room-context", () => ({
  RoomContext: ({ detail: current, playback }: { detail: RaceRoomDetailResponse; playback: RoomPlayback }) => (
    <output data-testid="authoritative-room">{JSON.stringify({
      status: current.room.status,
      mode: current.room.mode,
      sourceAvailability: current.room.source_availability,
      replayAvailable: current.room.replay_available,
      resultsAvailable: current.room.results_available,
      sessionKey: current.room.session_key,
      eventSlug: current.room.event_slug,
      meetingKey: current.room.meeting_key,
      eligibilityStatus: current.room.eligibility_status,
      ingestionStatus: current.room.ingestion_status,
      playbackLap: playback.current_lap,
    })}</output>
  ),
}));
vi.mock("@/components/race-rooms/evidence-drawer", () => ({ EvidenceDrawer: () => null }));

class FakeEventSource {
  static instances: FakeEventSource[] = [];

  readonly listeners = new Map<string, EventListener[]>();
  closed = false;

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: EventListener) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  close() {
    this.closed = true;
  }

  emit(type: string, data?: string) {
    const event = new MessageEvent(type, { data });
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

function roomDetail(sessionKey: string | null = null) {
  return {
    ...detail,
    room: {
      ...detail.room,
      status: "live" as const,
      mode: "live" as const,
      session_key: sessionKey,
    },
  };
}

async function flushBootstrap() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("RoomExperience live bootstrap", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.useFakeTimers();
    vi.stubGlobal("EventSource", FakeEventSource);
    api.getRaceRoom.mockReset();
    api.getRoomMessages.mockReset().mockResolvedValue({ messages: [], next_cursor: null });
    api.roomStreamUrl.mockClear();
    api.startRoomReplay.mockReset();
    api.updateRoomPlayback.mockReset();
    api.verifyReplayOperator.mockReset().mockResolvedValue({ authorized: true });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("attaches a published provider key to a waiting live room without a page refresh", async () => {
    api.getRaceRoom
      .mockResolvedValueOnce(roomDetail())
      .mockResolvedValue(roomDetail("published-key"));
    const view = render(<RoomExperience slug="test-room" />);
    await flushBootstrap();
    expect(screen.getByTestId("session-key")).toHaveTextContent("waiting");
    expect(screen.getByTestId("location-data-mode")).toHaveTextContent("mutable");

    await act(async () => { await vi.advanceTimersByTimeAsync(15_000); });

    expect(screen.getByTestId("session-key")).toHaveTextContent("published-key");
    view.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("marks archived room location windows immutable", async () => {
    api.getRaceRoom.mockResolvedValue({
      ...detail,
      room: { ...detail.room, status: "completed", mode: "archived" },
    });
    const view = render(<RoomExperience slug="test-room" />);
    await flushBootstrap();

    expect(screen.getByTestId("location-data-mode")).toHaveTextContent("immutable");

    view.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("keeps the room usable and retries when provider-key polling temporarily degrades", async () => {
    api.getRaceRoom
      .mockResolvedValueOnce(roomDetail())
      .mockRejectedValueOnce(new Error("provider unavailable"))
      .mockResolvedValue(roomDetail("published-key"));
    const view = render(<RoomExperience slug="test-room" />);
    await flushBootstrap();

    await act(async () => { await vi.advanceTimersByTimeAsync(15_000); });
    expect(screen.getByTestId("session-key")).toHaveTextContent("waiting");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    await act(async () => { await vi.advanceTimersByTimeAsync(15_000); });
    expect(screen.getByTestId("session-key")).toHaveTextContent("published-key");
    view.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("schedules only one reconnect when the same stream reports repeated errors", async () => {
    api.getRaceRoom.mockResolvedValue(roomDetail("race-1"));
    const view = render(<RoomExperience slug="test-room" />);
    await flushBootstrap();
    const first = FakeEventSource.instances[0];

    act(() => {
      first.emit("error");
      first.emit("error");
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });

    expect(FakeEventSource.instances).toHaveLength(2);
    view.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("ignores late events from a stream that has already reconnected", async () => {
    api.getRaceRoom.mockResolvedValue(roomDetail("race-1"));
    const view = render(<RoomExperience slug="test-room" />);
    await flushBootstrap();
    const first = FakeEventSource.instances[0];

    act(() => first.emit("error"));
    await act(async () => { await vi.advanceTimersByTimeAsync(1_500); });
    expect(FakeEventSource.instances).toHaveLength(2);

    act(() => first.emit("room_status", JSON.stringify({ status: "completed", current_lap: 53 })));

    expect(screen.getByText("live")).toBeVisible();
    expect(screen.queryByText("completed")).not.toBeInTheDocument();
    view.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("keeps the last usable room when a malformed stream frame arrives", async () => {
    api.getRaceRoom.mockResolvedValue(roomDetail("race-1"));
    const view = render(<RoomExperience slug="test-room" />);
    await flushBootstrap();

    expect(() => {
      act(() => FakeEventSource.instances[0].emit("room_status", "not json"));
    }).not.toThrow();

    expect(screen.getByText("live")).toBeVisible();
    view.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each([
    ["empty", {}],
    ["non-numeric sequence", { ...message(), sequence: "NaN" }],
    ["foreign room", { ...message(), room_id: "00000000-0000-0000-0000-000000000099", sequence: 7 }],
  ])("rejects a %s room message without corrupting the reconnect cursor", async (_label, payload) => {
    api.getRaceRoom.mockResolvedValue(roomDetail("race-1"));
    const view = render(<RoomExperience slug="test-room" />);
    await flushBootstrap();

    act(() => {
      FakeEventSource.instances[0].emit("room_message", JSON.stringify(payload));
      FakeEventSource.instances[0].emit("error");
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(1_500); });

    expect(api.roomStreamUrl).toHaveBeenLastCalledWith("test-room", 0);
    view.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each([
    ["empty", {}],
    ["missing event cursor", { ...detail.playback, current_event_sequence: undefined, current_lap: 9 }],
    ["non-numeric message cursor", { ...detail.playback, current_message_sequence: "NaN", current_lap: 9 }],
    ["playback speed below the backend minimum", { ...detail.playback, playback_speed: 0.25, current_lap: 9 }],
    ["playback speed above the backend maximum", { ...detail.playback, playback_speed: 9, current_lap: 9 }],
    ["foreign room", { ...detail.playback, room_id: "00000000-0000-0000-0000-000000000099", current_lap: 9 }],
  ])("rejects a %s playback state", async (_label, payload) => {
    api.getRaceRoom.mockResolvedValue(roomDetail("race-1"));
    const view = render(<RoomExperience slug="test-room" />);
    await flushBootstrap();
    const progress = screen.getByText("Current lap").parentElement;
    expect(progress).not.toBeNull();

    act(() => FakeEventSource.instances[0].emit("playback_state", JSON.stringify(payload)));

    expect(within(progress!).getByText("0")).toBeVisible();
    view.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each([
    ["invalid mode", { status: "completed", mode: "finished" }],
    ["retired development mode", { status: "completed", mode: "development" }],
    ["non-numeric lap", { status: "completed", mode: "archived", current_lap: "NaN" }],
    ["foreign room", { status: "completed", mode: "archived", room_id: "00000000-0000-0000-0000-000000000099" }],
    ["foreign session", { status: "completed", mode: "archived", session_key: "race-2" }],
  ])("rejects a room status with %s", async (_label, payload) => {
    api.getRaceRoom.mockResolvedValue(roomDetail("race-1"));
    const view = render(<RoomExperience slug="test-room" />);
    await flushBootstrap();

    act(() => FakeEventSource.instances[0].emit("room_status", JSON.stringify(payload)));

    expect(screen.getByText("live")).toBeVisible();
    expect(screen.getByTestId("location-data-mode")).toHaveTextContent("mutable");
    view.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("does not let a late metadata poll overwrite a newer streamed lap", async () => {
    const polling = deferred<ReturnType<typeof roomDetail>>();
    const initial = roomDetail("race-1");
    initial.room.current_lap = 1;
    initial.playback = { ...initial.playback, current_lap: null };
    const stale = roomDetail("race-1");
    stale.room.current_lap = 1;
    api.getRaceRoom
      .mockResolvedValueOnce(initial)
      .mockReturnValueOnce(polling.promise);
    const view = render(<RoomExperience slug="test-room" />);
    await flushBootstrap();

    await act(async () => { await vi.advanceTimersByTimeAsync(15_000); });
    act(() => FakeEventSource.instances[0].emit(
      "room_status",
      JSON.stringify({ status: "live", current_lap: 5 }),
    ));
    const progress = screen.getByText("Current lap").parentElement;
    expect(progress).not.toBeNull();
    expect(within(progress!).getByText("5")).toBeVisible();

    await act(async () => polling.resolve(stale));

    expect(within(progress!).getByText("5")).toBeVisible();
    view.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("reconciles authoritative terminal metadata after a completion frame and retries a failed hydration", async () => {
    const initial = roomDetail("race-live");
    initial.room.source_availability = "timing_only";
    initial.room.replay_available = false;
    initial.room.results_available = false;
    const terminal: RaceRoomDetailResponse = {
      ...roomDetail("race-archive"),
      room: {
        ...roomDetail("race-archive").room,
        status: "completed",
        mode: "archived",
        source_availability: "telemetry",
        replay_available: true,
        results_available: true,
        event_slug: "2026-italian-grand-prix",
        meeting_key: "meeting-42",
        eligibility_status: "already_exists",
        ingestion_status: "completed",
      },
      playback: {
        ...detail.playback,
        room_id: detail.room.id,
        current_event_sequence: 321,
        current_message_sequence: 87,
        current_lap: 53,
      },
    };
    api.getRaceRoom
      .mockResolvedValueOnce(initial)
      .mockRejectedValueOnce(new Error("terminal detail still publishing"))
      .mockResolvedValue(terminal);
    const view = render(<RoomExperience slug="test-room" />);
    await flushBootstrap();

    act(() => FakeEventSource.instances[0].emit(
      "room_status",
      JSON.stringify({ status: "completed", mode: "archived", current_lap: 53 }),
    ));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });

    expect(api.getRaceRoom).toHaveBeenCalledTimes(2);
    expect(screen.getByText("completed")).toBeVisible();
    expect(screen.getByTestId("authoritative-room")).toHaveTextContent('"sessionKey":"race-live"');

    await act(async () => { await vi.advanceTimersByTimeAsync(15_000); });

    expect(api.getRaceRoom).toHaveBeenCalledTimes(3);
    expect(screen.getByTestId("authoritative-room")).toHaveTextContent('"mode":"archived"');
    expect(screen.getByTestId("authoritative-room")).toHaveTextContent('"sourceAvailability":"telemetry"');
    expect(screen.getByTestId("authoritative-room")).toHaveTextContent('"replayAvailable":true');
    expect(screen.getByTestId("authoritative-room")).toHaveTextContent('"resultsAvailable":true');
    expect(screen.getByTestId("authoritative-room")).toHaveTextContent('"sessionKey":"race-archive"');
    expect(screen.getByTestId("authoritative-room")).toHaveTextContent('"eventSlug":"2026-italian-grand-prix"');
    expect(screen.getByTestId("authoritative-room")).toHaveTextContent('"meetingKey":"meeting-42"');
    expect(screen.getByTestId("authoritative-room")).toHaveTextContent('"eligibilityStatus":"already_exists"');
    expect(screen.getByTestId("authoritative-room")).toHaveTextContent('"ingestionStatus":"completed"');
    expect(screen.getByTestId("authoritative-room")).toHaveTextContent('"playbackLap":53');

    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(api.getRaceRoom).toHaveBeenCalledTimes(3);
    view.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("hydrates terminal playback when polling observes completion before the stream", async () => {
    const initial = roomDetail("race-live");
    const terminal: RaceRoomDetailResponse = {
      ...initial,
      room: {
        ...initial.room,
        status: "completed",
        mode: "archived",
        source_availability: "telemetry",
        replay_available: true,
        results_available: true,
      },
      playback: {
        ...initial.playback,
        current_event_sequence: 321,
        current_message_sequence: 87,
        current_lap: 53,
        playback_speed: 8,
        is_paused: false,
      },
    };
    api.getRaceRoom
      .mockResolvedValueOnce(initial)
      .mockResolvedValue(terminal);
    const view = render(<RoomExperience slug="test-room" />);
    await flushBootstrap();
    expect(screen.getByTestId("authoritative-room")).toHaveTextContent('"playbackLap":0');

    await act(async () => { await vi.advanceTimersByTimeAsync(15_000); });

    expect(screen.getByText("completed")).toBeVisible();
    expect(screen.getByTestId("location-data-mode")).toHaveTextContent("immutable");
    expect(screen.getByTestId("authoritative-room")).toHaveTextContent('"playbackLap":53');
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(api.getRaceRoom).toHaveBeenCalledTimes(2);
    view.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("aborts an in-flight provider-key poll and clears every timer on unmount", async () => {
    const polling = deferred<ReturnType<typeof roomDetail>>();
    api.getRaceRoom
      .mockResolvedValueOnce(roomDetail())
      .mockReturnValueOnce(polling.promise);
    const view = render(<RoomExperience slug="test-room" />);
    await flushBootstrap();

    await act(async () => { await vi.advanceTimersByTimeAsync(15_000); });
    const signal = api.getRaceRoom.mock.calls[1][1] as AbortSignal;
    expect(signal.aborted).toBe(false);

    view.unmount();

    expect(signal.aborted).toBe(true);
    expect(FakeEventSource.instances.every((source) => source.closed)).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("verifies in memory, sends the password only on replay mutations, and locks on 401", async () => {
    const canary = "room-operator-canary";
    api.getRaceRoom.mockResolvedValue(detail);
    api.startRoomReplay.mockRejectedValue({ status: 401 });
    const view = render(<RoomExperience slug="test-room" />);
    await flushBootstrap();

    fireEvent.click(screen.getByRole("button", { name: /unlock controls/i }));
    fireEvent.change(screen.getByLabelText("Operator password"), { target: { value: canary } });
    fireEvent.submit(screen.getByRole("form", { name: "Unlock replay controls" }));
    await flushBootstrap();

    expect(api.verifyReplayOperator).toHaveBeenCalledWith(canary);
    expect(screen.queryByLabelText("Operator password")).not.toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(canary);
    expect(window.location.href).not.toContain(canary);
    expect(JSON.stringify({ ...window.localStorage })).not.toContain(canary);

    fireEvent.click(screen.getByRole("button", { name: /start replay/i }));
    await flushBootstrap();

    expect(api.startRoomReplay).toHaveBeenCalledWith("test-room", "start", canary);
    expect(screen.getByRole("form", { name: "Unlock replay controls" })).toBeVisible();
    expect(screen.queryByRole("button", { name: /start replay/i })).not.toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(canary);
    view.unmount();
  });

  it("clears operator access when the room slug changes", async () => {
    const canary = "session-change-canary";
    api.getRaceRoom.mockResolvedValue(detail);
    const view = render(<RoomExperience slug="first-room" />);
    await flushBootstrap();

    fireEvent.click(screen.getByRole("button", { name: /unlock controls/i }));
    fireEvent.change(screen.getByLabelText("Operator password"), { target: { value: canary } });
    fireEvent.submit(screen.getByRole("form", { name: "Unlock replay controls" }));
    await flushBootstrap();
    expect(screen.getByRole("button", { name: /start replay/i })).toBeVisible();

    view.rerender(<RoomExperience slug="second-room" />);
    await flushBootstrap();

    expect(screen.getByRole("button", { name: /unlock controls/i })).toBeVisible();
    expect(screen.queryByRole("button", { name: /start replay/i })).not.toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(canary);
    view.unmount();
  });

  it("ignores a successful unlock that resolves after the room slug changes", async () => {
    const verification = deferred<{ authorized: true }>();
    api.getRaceRoom.mockResolvedValue(detail);
    api.verifyReplayOperator.mockReturnValue(verification.promise);
    const view = render(<RoomExperience slug="first-room" />);
    await flushBootstrap();

    fireEvent.click(screen.getByRole("button", { name: /unlock controls/i }));
    fireEvent.change(screen.getByLabelText("Operator password"), { target: { value: "late-unlock-canary" } });
    fireEvent.submit(screen.getByRole("form", { name: "Unlock replay controls" }));
    view.rerender(<RoomExperience slug="second-room" />);
    await flushBootstrap();
    await act(async () => { verification.resolve({ authorized: true }); });

    expect(screen.getByRole("button", { name: /unlock controls/i })).toBeVisible();
    expect(screen.queryByRole("button", { name: /start replay/i })).not.toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("late-unlock-canary");
    view.unmount();
  });

  it("ignores a successful replay mutation that resolves after the room slug changes", async () => {
    const mutation = deferred<RaceRoomDetailResponse>();
    const secondRoom = {
      ...detail,
      room: { ...detail.room, slug: "second-room", current_lap: 7 },
      playback: { ...detail.playback, current_lap: 7 },
    };
    const staleResponse = {
      ...detail,
      room: { ...detail.room, slug: "first-room", status: "completed" as const, current_lap: 12 },
      playback: { ...detail.playback, current_lap: 12 },
    };
    api.getRaceRoom.mockImplementation((requestedSlug: string) => (
      Promise.resolve(requestedSlug === "second-room" ? secondRoom : detail)
    ));
    api.startRoomReplay.mockReturnValue(mutation.promise);
    const view = render(<RoomExperience slug="first-room" />);
    await flushBootstrap();

    fireEvent.click(screen.getByRole("button", { name: /unlock controls/i }));
    fireEvent.change(screen.getByLabelText("Operator password"), { target: { value: "late-mutation-canary" } });
    fireEvent.submit(screen.getByRole("form", { name: "Unlock replay controls" }));
    await flushBootstrap();
    fireEvent.click(screen.getByRole("button", { name: /start replay/i }));
    view.rerender(<RoomExperience slug="second-room" />);
    await flushBootstrap();
    await act(async () => { mutation.resolve(staleResponse); });

    expect(screen.getByTestId("authoritative-room")).toHaveTextContent('"playbackLap":7');
    expect(screen.getByTestId("authoritative-room")).not.toHaveTextContent('"status":"completed"');
    expect(screen.getByRole("button", { name: /unlock controls/i })).toBeVisible();
    expect(document.body).not.toHaveTextContent("late-mutation-canary");
    view.unmount();
  });
});
