// SPDX-License-Identifier: AGPL-3.0-only
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useDriverLocations } from "@/lib/use-driver-locations";
import type { DriverLocationSample } from "@/lib/types";

const api = vi.hoisted(() => ({
  getSessionTrack: vi.fn(),
  getSessionLocationSamples: vi.fn(),
}));

vi.mock("@/lib/api", () => api);

function track(sessionKey: string, available = false) {
  return {
    session_key: sessionKey,
    available,
    bounds: available ? { min_x: 1, max_x: 2, min_y: 3, max_y: 4 } : null,
    path: available ? [{ x: 1, y: 3 }, { x: 2, y: 4 }] : [],
    source_driver_number: available ? 63 : null,
    sample_count: available ? 2 : 0,
    first_sample_at: available ? "2026-09-06T13:00:00Z" : null,
    last_sample_at: available ? "2026-09-06T13:00:01Z" : null,
  };
}

const unavailableTrack = track("race-1");

const availableTrack = track("race-1", true);

function sample(driverNumber = 63): DriverLocationSample {
  return {
    driver_number: driverNumber,
    x: 1,
    y: 3,
    z: 0,
    sample_time: "2026-09-06T13:00:01Z",
  };
}

function locations(samples: DriverLocationSample[], sessionKey = "race-1") {
  return {
    locations: {
      session_key: sessionKey,
      count: samples.length,
      drivers: [...new Set(samples.map((item) => item.driver_number))],
      since: null,
      until: null,
      samples,
    },
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, reject, resolve };
}

describe("useDriverLocations", () => {
  beforeEach(() => {
    api.getSessionTrack.mockReset().mockImplementation((sessionKey: string) =>
      Promise.resolve({ track: track(sessionKey) }),
    );
    api.getSessionLocationSamples.mockReset().mockImplementation((sessionKey: string) =>
      Promise.resolve(locations([], sessionKey)),
    );
  });

  it("refreshes missing track geometry after live location fixes begin", async () => {
    api.getSessionTrack
      .mockResolvedValueOnce({ track: unavailableTrack })
      .mockResolvedValueOnce({ track: availableTrack });
    const { result, rerender } = renderHook(
      ({ liveSamples }) => useDriverLocations({ sessionKey: "race-1", clockIso: "2026-09-06T13:00:00Z", liveSamples }),
      { initialProps: { liveSamples: [] as DriverLocationSample[] } },
    );
    await waitFor(() => expect(api.getSessionTrack).toHaveBeenCalledOnce());

    rerender({ liveSamples: [sample()] });

    await waitFor(() => expect(api.getSessionTrack).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(result.current.track?.available).toBe(true));
  });

  it("does not let an older track response overwrite recovered geometry", async () => {
    let resolveInitial: ((value: { track: typeof unavailableTrack }) => void) | undefined;
    api.getSessionTrack
      .mockReturnValueOnce(new Promise((resolve) => { resolveInitial = resolve; }))
      .mockResolvedValueOnce({ track: availableTrack });
    const liveSamples = [sample()];
    const { result } = renderHook(() => useDriverLocations({
      sessionKey: "race-1",
      clockIso: "2026-09-06T13:00:01Z",
      liveSamples,
    }));
    await waitFor(() => expect(result.current.track?.available).toBe(true));

    await act(async () => resolveInitial?.({ track: unavailableTrack }));

    expect(result.current.track?.available).toBe(true);
  });

  it("accepts available geometry while a newer recovery request is still pending", async () => {
    const initialTrack = deferred<{ track: ReturnType<typeof track> }>();
    const recoveryTrack = deferred<{ track: ReturnType<typeof track> }>();
    const liveSamples = [sample()];
    api.getSessionTrack
      .mockReturnValueOnce(initialTrack.promise)
      .mockReturnValueOnce(recoveryTrack.promise);
    const { result } = renderHook(() => useDriverLocations({
      sessionKey: "race-1",
      clockIso: "2026-09-06T13:00:01Z",
      liveSamples,
    }));
    await waitFor(() => expect(api.getSessionTrack).toHaveBeenCalledTimes(2));

    await act(async () => initialTrack.resolve({ track: availableTrack }));

    expect(result.current.track?.available).toBe(true);
  });

  it("ignores a late track failure from the previous session", async () => {
    const firstTrack = deferred<{ track: ReturnType<typeof track> }>();
    api.getSessionTrack.mockImplementation((sessionKey: string) =>
      sessionKey === "race-1"
        ? firstTrack.promise
        : Promise.resolve({ track: track("race-2", true) }),
    );
    const view = renderHook(
      ({ sessionKey }) => useDriverLocations({
        sessionKey,
        clockIso: "2026-09-06T13:00:01Z",
      }),
      { initialProps: { sessionKey: "race-1" } },
    );

    view.rerender({ sessionKey: "race-2" });
    await waitFor(() => expect(view.result.current.track?.session_key).toBe("race-2"));
    await act(async () => firstTrack.reject(new Error("old session failed")));

    expect(view.result.current.track?.session_key).toBe("race-2");
    expect(view.result.current.track?.available).toBe(true);
  });

  it("ignores a late location failure from the previous session", async () => {
    const firstLocations = deferred<ReturnType<typeof locations>>();
    api.getSessionTrack.mockImplementation((sessionKey: string) =>
      Promise.resolve({ track: track(sessionKey, true) }),
    );
    api.getSessionLocationSamples.mockImplementation((sessionKey: string) =>
      sessionKey === "race-1"
        ? firstLocations.promise
        : Promise.resolve(locations([], "race-2")),
    );
    const view = renderHook(
      ({ sessionKey }) => useDriverLocations({
        sessionKey,
        clockIso: "2026-09-06T13:00:01Z",
      }),
      { initialProps: { sessionKey: "race-1" } },
    );

    view.rerender({ sessionKey: "race-2" });
    await waitFor(() => expect(view.result.current.track?.session_key).toBe("race-2"));
    await act(async () => firstLocations.reject(new Error("old location window failed")));

    expect(view.result.current.track?.session_key).toBe("race-2");
    expect(view.result.current.status).not.toBe("error");
  });

  it("does not copy live fixes from the previous session during a session change", async () => {
    const previousSamples = [sample()];
    const view = renderHook(
      ({ sessionKey, liveSamples }) => useDriverLocations({
        sessionKey,
        clockIso: "2026-09-06T13:00:01Z",
        liveSamples,
      }),
      { initialProps: { sessionKey: "race-1", liveSamples: previousSamples } },
    );
    await waitFor(() => expect(view.result.current.driverNumbers).toEqual([63]));

    view.rerender({ sessionKey: "race-2", liveSamples: previousSamples });

    await waitFor(() => expect(view.result.current.track?.session_key).toBe("race-2"));
    expect(view.result.current.driverNumbers).toEqual([]);
    expect(view.result.current.sampleAt(Date.parse("2026-09-06T13:00:01Z"))).toEqual([]);
  });

  it("keeps live positions ready when track discovery degrades", async () => {
    const pendingTrack = deferred<{ track: ReturnType<typeof track> }>();
    const liveSamples = [sample()];
    api.getSessionTrack.mockReturnValue(pendingTrack.promise);
    const { result } = renderHook(() => useDriverLocations({
      sessionKey: "race-1",
      clockIso: "2026-09-06T13:00:01Z",
      liveSamples,
    }));
    await waitFor(() => expect(result.current.status).toBe("ready"));

    await act(async () => pendingTrack.reject(new Error("track temporarily unavailable")));

    expect(result.current.status).toBe("ready");
    expect(result.current.driverNumbers).toEqual([63]);
  });

  it("settles an empty session as having no samples", async () => {
    const { result } = renderHook(() => useDriverLocations({
      sessionKey: "race-1",
      clockIso: "2026-09-06T13:00:01Z",
    }));

    await waitFor(() => expect(result.current.status).toBe("no-samples"));
    expect(result.current.driverNumbers).toEqual([]);
  });

  it("loads fixed windows around the authoritative replay clock", async () => {
    const { result } = renderHook(() => useDriverLocations({
      sessionKey: "race-1",
      clockIso: "2026-09-06T13:00:01Z",
    }));

    await waitFor(() => expect(api.getSessionLocationSamples).toHaveBeenCalledTimes(3));

    expect(api.getSessionLocationSamples).toHaveBeenNthCalledWith(1, "race-1", {
      since: "2026-09-06T12:59:30.000Z",
      until: "2026-09-06T13:00:00.000Z",
      limit: 20_000,
    }, expect.any(AbortSignal));
    expect(api.getSessionLocationSamples).toHaveBeenNthCalledWith(3, "race-1", {
      since: "2026-09-06T13:00:30.000Z",
      until: "2026-09-06T13:01:00.000Z",
      limit: 20_000,
    }, expect.any(AbortSignal));
    expect(result.current.currentClockMs()).toBe(Date.parse("2026-09-06T13:00:01Z"));
  });

  it("refetches an active partial time window as the live clock advances", async () => {
    const { rerender } = renderHook(
      ({ clockIso }) => useDriverLocations({ sessionKey: "race-1", clockIso }),
      { initialProps: { clockIso: "2026-09-06T13:00:01Z" } },
    );
    await waitFor(() => expect(api.getSessionLocationSamples).toHaveBeenCalled());
    const initialCalls = api.getSessionLocationSamples.mock.calls.length;

    act(() => rerender({ clockIso: "2026-09-06T13:00:02Z" }));

    await waitFor(() => expect(api.getSessionLocationSamples.mock.calls.length).toBeGreaterThan(initialCalls));
  });
});
