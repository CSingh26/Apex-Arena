// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getSessionLocationSamples, getSessionTrack } from "@/lib/api";
import {
  appendSamples,
  pruneSeries,
  selectLocationsAt,
  seriesSampleCount,
  type DriverLocationState,
  type SampleSeries,
} from "@/lib/driver-locations";
import type { DriverLocationSample, SessionTrackState } from "@/lib/types";

export type LocationStatus = "idle" | "loading" | "ready" | "no-samples" | "error";

export type LocationDebug = {
  sessionKey: string | null;
  status: LocationStatus;
  loadedSamples: number;
  loadedWindows: number;
  driversLocated: number;
  clockIso: string | null;
  trackPoints: number;
  trackSampleCount: number;
  bounds: SessionTrackState["bounds"];
};

/** Samples are fetched in fixed absolute-time windows so they cache cleanly. */
const WINDOW_MS = 30_000;
/** Look-behind keeps a fix available immediately after a seek. */
const WINDOW_LOOKBEHIND_MS = 10_000;
/** Look-ahead gives interpolation a second point before the clock arrives. */
const WINDOW_LOOKAHEAD_MS = 30_000;
const SERIES_RETENTION_MS = 180_000;
const MAX_REPLAY_RATE = 16;

type ClockAnchor = { target: number; anchorTarget: number; anchorPerf: number; rate: number };

/**
 * Everything mutable is stamped with the session it belongs to, so a room
 * change can never show the previous session's cars and two rooms can never
 * share a cached window.
 */
type SessionCache = {
  sessionKey: string | null;
  series: SampleSeries;
  loadedKeys: Set<string>;
  inFlight: Set<string>;
  clock: ClockAnchor | null;
};

type LoadState = {
  sessionKey: string | null;
  track: SessionTrackState | null;
  status: LocationStatus;
  driverNumbers: number[];
  loadedSamples: number;
  loadedWindows: number;
};

const EMPTY_LOAD: LoadState = {
  sessionKey: null,
  track: null,
  status: "idle",
  driverNumbers: [],
  loadedSamples: 0,
  loadedWindows: 0,
};

type Options = {
  sessionKey: string | null;
  /** Authoritative session clock: the replay/live event time, in UTC ISO. */
  clockIso: string | null;
  /** Live fixes arriving on the session stream, merged into the same series. */
  liveSamples?: readonly DriverLocationSample[];
  enabled?: boolean;
};

export type DriverLocationsResult = {
  track: SessionTrackState | null;
  status: LocationStatus;
  driverNumbers: number[];
  sampleAt: (clockMs: number) => DriverLocationState[];
  currentClockMs: () => number;
  debug: LocationDebug;
};

function windowIndex(timeMs: number): number {
  return Math.floor(timeMs / WINDOW_MS);
}

function nowPerf(): number {
  return typeof performance !== "undefined" ? performance.now() : Date.now();
}

function emptyCache(sessionKey: string | null): SessionCache {
  return {
    sessionKey,
    series: new Map(),
    loadedKeys: new Set(),
    inFlight: new Set(),
    clock: null,
  };
}

function sameNumbers(left: number[], right: number[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

export function useDriverLocations({
  sessionKey,
  clockIso,
  liveSamples,
  enabled = true,
}: Options): DriverLocationsResult {
  const [loadState, setLoadState] = useState<LoadState>(EMPTY_LOAD);
  const cacheRef = useRef<SessionCache>(emptyCache(null));

  // Stale state from a previous session is ignored rather than cleared, so no
  // render ever depends on a reset having already happened.
  const current: LoadState =
    loadState.sessionKey === sessionKey
      ? loadState
      : {
          ...EMPTY_LOAD,
          sessionKey,
          status: sessionKey && enabled ? "loading" : "idle",
        };

  const update = useCallback(
    (key: string | null, change: (previous: LoadState) => Partial<LoadState>) => {
      setLoadState((previous) => {
        const base = previous.sessionKey === key ? previous : { ...EMPTY_LOAD, sessionKey: key };
        return { ...base, ...change(base) };
      });
    },
    [],
  );

  const cacheFor = useCallback((key: string | null) => {
    if (cacheRef.current.sessionKey !== key) cacheRef.current = emptyCache(key);
    return cacheRef.current;
  }, []);

  useEffect(() => {
    if (!sessionKey || !enabled) return;
    cacheFor(sessionKey);
    const controller = new AbortController();
    getSessionTrack(sessionKey, controller.signal)
      .then((response) => update(sessionKey, () => ({ track: response.track })))
      .catch((reason: Error) => {
        if (reason.name !== "AbortError") update(sessionKey, () => ({ status: "error" }));
      });
    return () => controller.abort();
  }, [cacheFor, enabled, sessionKey, update]);

  const targetMs = useMemo(() => {
    const parsed = clockIso ? Date.parse(clockIso) : Number.NaN;
    return Number.isFinite(parsed) ? parsed : null;
  }, [clockIso]);

  // Track how fast session time is advancing so motion between clock updates is
  // driven by the feed's own pace rather than a guessed animation speed.
  useEffect(() => {
    if (targetMs == null) return;
    const cache = cacheFor(sessionKey);
    const perf = nowPerf();
    const previous = cache.clock;
    if (!previous || targetMs < previous.target) {
      // First clock, or a deliberate seek backwards: snap, do not glide.
      cache.clock = { target: targetMs, anchorTarget: targetMs, anchorPerf: perf, rate: 0 };
      return;
    }
    const elapsed = perf - previous.anchorPerf;
    const observed = elapsed > 0 ? (targetMs - previous.target) / elapsed : previous.rate;
    const rate = Math.min(MAX_REPLAY_RATE, Math.max(0, observed));
    cache.clock = {
      target: targetMs,
      anchorTarget: targetMs,
      anchorPerf: perf,
      // Smooth the estimate: replay ticks are not evenly spaced in session time.
      rate: previous.rate === 0 ? rate : previous.rate * 0.6 + rate * 0.4,
    };
  }, [cacheFor, sessionKey, targetMs]);

  const currentClockMs = useCallback(() => {
    const anchor = cacheRef.current.sessionKey === sessionKey ? cacheRef.current.clock : null;
    if (!anchor) return targetMs ?? Date.now();
    const projected = anchor.anchorTarget + anchor.rate * (nowPerf() - anchor.anchorPerf);
    // Never run ahead of the newest known session time: when playback pauses,
    // the target stops advancing and the cars stop with it.
    return Math.min(anchor.target, projected);
  }, [sessionKey, targetMs]);

  // Load the windows the clock is about to need.
  useEffect(() => {
    if (!sessionKey || !enabled || targetMs == null) return;
    const cache = cacheFor(sessionKey);
    let cancelled = false;
    const controller = new AbortController();

    const load = async () => {
      const first = windowIndex(targetMs - WINDOW_LOOKBEHIND_MS);
      const last = windowIndex(targetMs + WINDOW_LOOKAHEAD_MS);
      let loadedAny = false;
      for (let index = first; index <= last; index += 1) {
        const key = `${sessionKey}:${index}`;
        if (cache.loadedKeys.has(key) || cache.inFlight.has(key)) continue;
        cache.inFlight.add(key);
        try {
          const response = await getSessionLocationSamples(
            sessionKey,
            {
              since: new Date(index * WINDOW_MS).toISOString(),
              until: new Date((index + 1) * WINDOW_MS).toISOString(),
              limit: 20_000,
            },
            controller.signal,
          );
          if (cancelled) return;
          cache.loadedKeys.add(key);
          cache.series = appendSamples(cache.series, response.locations.samples);
          loadedAny = true;
        } catch (reason) {
          if ((reason as Error).name === "AbortError") return;
          update(sessionKey, (previous) =>
            previous.status === "ready" ? {} : { status: "error" },
          );
        } finally {
          cache.inFlight.delete(key);
        }
      }
      if (cancelled || !loadedAny) return;
      cache.series = pruneSeries(cache.series, targetMs - SERIES_RETENTION_MS);
      const numbers = [...cache.series.keys()].sort((left, right) => left - right);
      update(sessionKey, (previous) => ({
        driverNumbers: sameNumbers(previous.driverNumbers, numbers)
          ? previous.driverNumbers
          : numbers,
        loadedSamples: seriesSampleCount(cache.series),
        loadedWindows: cache.loadedKeys.size,
        status: numbers.length ? "ready" : "no-samples",
      }));
    };

    void load();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [cacheFor, enabled, sessionKey, targetMs, update]);

  // Live fixes go into the same series, so live and replay render identically.
  useEffect(() => {
    if (!liveSamples?.length) return;
    const cache = cacheFor(sessionKey);
    cache.series = appendSamples(cache.series, liveSamples);
    const numbers = [...cache.series.keys()].sort((left, right) => left - right);
    update(sessionKey, (previous) => ({
      driverNumbers: sameNumbers(previous.driverNumbers, numbers)
        ? previous.driverNumbers
        : numbers,
      loadedSamples: seriesSampleCount(cache.series),
      status: numbers.length ? "ready" : previous.status,
    }));
  }, [cacheFor, liveSamples, sessionKey, update]);

  // Derived, not stored: geometry reporting zero fixes settles whether this
  // session has positions at all, without another round trip.
  const status: LocationStatus =
    current.status === "loading" && current.track !== null && current.track.sample_count === 0
      ? "no-samples"
      : current.status;

  const sampleAt = useCallback(
    (clockMs: number) =>
      cacheRef.current.sessionKey === sessionKey
        ? selectLocationsAt(cacheRef.current.series, clockMs)
        : [],
    [sessionKey],
  );

  const debug = useMemo<LocationDebug>(
    () => ({
      sessionKey,
      status,
      loadedSamples: current.loadedSamples,
      loadedWindows: current.loadedWindows,
      driversLocated: current.driverNumbers.length,
      clockIso,
      trackPoints: current.track?.path.length ?? 0,
      trackSampleCount: current.track?.sample_count ?? 0,
      bounds: current.track?.bounds ?? null,
    }),
    [
      clockIso,
      current.driverNumbers.length,
      current.loadedSamples,
      current.loadedWindows,
      current.track,
      sessionKey,
      status,
    ],
  );

  return {
    track: current.track,
    status,
    driverNumbers: current.driverNumbers,
    sampleAt,
    currentClockMs,
    debug,
  };
}
