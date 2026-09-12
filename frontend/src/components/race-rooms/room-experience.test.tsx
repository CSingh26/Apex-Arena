// SPDX-License-Identifier: AGPL-3.0-only
import { act, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RoomExperience } from "@/components/race-rooms/room-experience";
import { detail } from "@/test/race-room-fixtures";

const api = vi.hoisted(() => ({
  getRaceRoom: vi.fn(),
  getRoomMessages: vi.fn(),
  roomStreamUrl: vi.fn(() => "/stream"),
}));

vi.mock("@/lib/api", () => api);
vi.mock("@/components/race-rooms/live-command-center", () => ({
  LiveCommandCenter: ({ sessionKey }: { sessionKey: string | null }) => (
    <div data-testid="session-key">{sessionKey ?? "waiting"}</div>
  ),
}));
vi.mock("@/components/race-rooms/room-context", () => ({ RoomContext: () => null }));
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

    await act(async () => { await vi.advanceTimersByTimeAsync(15_000); });

    expect(screen.getByTestId("session-key")).toHaveTextContent("published-key");
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
});
