// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import type { DriverLocationState } from "@/lib/driver-locations";
import {
  DEFAULT_VIEWPORT,
  boundsFromPoints,
  createTrackProjection,
  isUsableBounds,
  projectPath,
  projectPoint,
} from "@/lib/track-projection";
import type { LocationDebug, LocationStatus } from "@/lib/use-driver-locations";
import type { SessionTrackState } from "@/lib/types";

import styles from "./circuit-map.module.css";

export type CircuitMapDriver = { number: number; code: string; name: string };

type CircuitMapProps = {
  track: SessionTrackState | null;
  status: LocationStatus;
  driverNumbers: number[];
  drivers: Map<number, CircuitMapDriver>;
  selectedDriver: number | null;
  battleDrivers?: ReadonlySet<number>;
  onSelectDriver: (driverNumber: number) => void;
  sampleAt: (clockMs: number) => DriverLocationState[];
  currentClockMs: () => number;
  debug?: LocationDebug;
  showDebug?: boolean;
  /** Renders un-interpolated fixes so a projection bug can be told from a motion bug. */
  showRawPoints?: boolean;
  /**
   * Shown only when there is nothing to plot. Kept outside the SVG on purpose:
   * static circuit artwork lives in its own coordinate space and must never
   * share a viewBox with provider-derived markers.
   */
  emptyVisual?: ReactNode;
};

const MARKER_RADIUS = 13;
const SELECTED_RADIUS = 20;

export function CircuitMap({
  track,
  status,
  driverNumbers,
  drivers,
  selectedDriver,
  battleDrivers = new Set<number>(),
  onSelectDriver,
  sampleAt,
  currentClockMs,
  debug,
  showDebug = false,
  showRawPoints = false,
  emptyVisual,
}: CircuitMapProps) {
  const groupRefs = useRef(new Map<number, SVGGElement | null>());
  const [statusLine, setStatusLine] = useState<{ located: number; ageMs: number | null }>({
    located: 0,
    ageMs: null,
  });
  const [rawPoints, setRawPoints] = useState<DriverLocationState[]>([]);

  // Both the outline and every marker are placed by this one projection, so
  // they cannot drift apart. Falling back to the loaded fixes keeps the map
  // usable for a session whose geometry has not been derived yet.
  const projection = useMemo(() => {
    const bounds = isUsableBounds(track?.bounds)
      ? track.bounds
      : boundsFromPoints(sampleAt(currentClockMs()));
    return bounds && isUsableBounds(bounds)
      ? createTrackProjection(bounds, DEFAULT_VIEWPORT)
      : null;
    // currentClockMs/sampleAt are stable; the fallback only matters on first paint.
  }, [currentClockMs, sampleAt, track]);

  const trackPath = useMemo(
    () => (projection && track?.path.length ? projectPath(projection, track.path) : ""),
    [projection, track],
  );

  // Re-derived whenever the marker set changes so a newly located car is
  // placed on the frame it appears; the animation loop owns every update after.
  const initialPositions = useMemo(() => {
    const positions = new Map<number, { x: number; y: number }>();
    if (!projection) return positions;
    const states = new Map(
      sampleAt(currentClockMs()).map((state) => [state.driverNumber, state]),
    );
    for (const driverNumber of driverNumbers) {
      const state = states.get(driverNumber);
      if (state) positions.set(driverNumber, projectPoint(projection, state.x, state.y));
    }
    return positions;
  }, [currentClockMs, driverNumbers, projection, sampleAt]);

  const registerGroup = useCallback((driverNumber: number, element: SVGGElement | null) => {
    groupRefs.current.set(driverNumber, element);
  }, []);

  // Positions are written straight onto each marker's transform. React is not
  // re-rendered per frame, so 20 moving cars never re-render the Race Room.
  useEffect(() => {
    if (!projection) return;
    let frame = 0;
    let lastStatusAt = 0;
    const tick = () => {
      const clock = currentClockMs();
      const states = sampleAt(clock);
      let newest = -Infinity;
      for (const state of states) {
        const group = groupRefs.current.get(state.driverNumber);
        if (!group) continue;
        const point = projectPoint(projection, state.x, state.y);
        if (!Number.isFinite(point.x) || !Number.isFinite(point.y)) continue;
        group.setAttribute("transform", `translate(${point.x.toFixed(2)} ${point.y.toFixed(2)})`);
        group.dataset.stale = state.stale ? "true" : "false";
        newest = Math.max(newest, state.timestamp);
      }
      const now = typeof performance !== "undefined" ? performance.now() : Date.now();
      if (now - lastStatusAt > 400) {
        lastStatusAt = now;
        setStatusLine({
          located: states.length,
          ageMs: Number.isFinite(newest) ? Math.max(0, clock - newest) : null,
        });
        if (showRawPoints) setRawPoints(states);
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [currentClockMs, projection, sampleAt, showRawPoints]);

  const { width, height } = DEFAULT_VIEWPORT;
  const located = statusLine.located;

  if (status === "loading" || (status === "idle" && !track)) {
    return (
      <div className={styles.placeholder} role="status" aria-live="polite">
        <span className={styles.spinner} aria-hidden />
        <b>Loading circuit map</b>
        <p>Fetching the circuit trace and driver track positions for this session.</p>
      </div>
    );
  }

  if (status === "error") {
    return (
      <div className={styles.placeholder} role="status">
        {emptyVisual}
        <b>Track positions unavailable</b>
        <p>
          The position feed could not be reached. Timing, telemetry and the room conversation are
          unaffected.
        </p>
      </div>
    );
  }

  if (!projection || status === "no-samples") {
    return (
      <div className={styles.placeholder} role="status">
        {emptyVisual}
        <b>No track positions for this session</b>
        <p>
          OpenF1 published no car position data for this session, so the circuit map has nothing to
          plot. Everything else in the room still works.
        </p>
      </div>
    );
  }

  return (
    <div className={styles.wrapper}>
      <svg
        className={styles.map}
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label={`Driver track positions, ${located} cars located, ${battleDrivers.size} in active battles`}
      >
        {trackPath ? (
          <>
            <path className={styles.trackShadow} d={trackPath} />
            <path className={styles.trackLine} d={trackPath} />
          </>
        ) : null}

        {showRawPoints
          ? rawPoints.map((state) => {
              const point = projectPoint(projection, state.x, state.y);
              return (
                <circle
                  key={`raw-${state.driverNumber}`}
                  className={styles.rawPoint}
                  cx={point.x}
                  cy={point.y}
                  r={5}
                />
              );
            })
          : null}

        {driverNumbers.map((driverNumber) => {
          const driver = drivers.get(driverNumber);
          const selected = selectedDriver === driverNumber;
          const inBattle = battleDrivers.has(driverNumber);
          const label = driver?.code ?? String(driverNumber);
          const initial = initialPositions.get(driverNumber);
          return (
            <g
              key={driverNumber}
              ref={(element) => registerGroup(driverNumber, element)}
              className={`${styles.marker} ${inBattle ? styles.markerBattle : ""} ${selected ? styles.markerSelected : ""}`}
              // Placed on first render so a marker never flashes at the origin
              // before the animation loop takes over.
              transform={`translate(${initial?.x.toFixed(2) ?? 0} ${initial?.y.toFixed(2) ?? 0})`}
              role="button"
              tabIndex={0}
              aria-pressed={selected}
              aria-label={`Select ${driver?.name ?? `car ${driverNumber}`}`}
              onClick={() => onSelectDriver(driverNumber)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onSelectDriver(driverNumber);
                }
              }}
            >
              {inBattle ? <circle className={styles.battleRing} r={SELECTED_RADIUS + 5} /> : null}
              <circle className={styles.markerDot} r={selected ? SELECTED_RADIUS : MARKER_RADIUS} />
              <text className={styles.markerLabel} y={1}>
                {label.slice(0, 3)}
              </text>
            </g>
          );
        })}
      </svg>
      {battleDrivers.size ? (
        <p className="sr-only">Highlighted markers identify drivers in current timing-based battles.</p>
      ) : null}

      {showDebug && debug ? (
        <dl className={styles.debug} aria-label="Location debug">
          <div>
            <dt>Session key</dt>
            <dd>{debug.sessionKey ?? "—"}</dd>
          </div>
          <div>
            <dt>Status</dt>
            <dd>{debug.status}</dd>
          </div>
          <div>
            <dt>Samples loaded</dt>
            <dd>
              {debug.loadedSamples.toLocaleString()} ({debug.loadedWindows} windows)
            </dd>
          </div>
          <div>
            <dt>Drivers located</dt>
            <dd>
              {located}/{debug.driversLocated}
            </dd>
          </div>
          <div>
            <dt>Track points</dt>
            <dd>
              {debug.trackPoints} of {debug.trackSampleCount.toLocaleString()} fixes
            </dd>
          </div>
          <div>
            <dt>Bounds</dt>
            <dd>
              {debug.bounds
                ? `X ${Math.round(debug.bounds.min_x)} → ${Math.round(debug.bounds.max_x)} · Y ${Math.round(debug.bounds.min_y)} → ${Math.round(debug.bounds.max_y)}`
                : "—"}
            </dd>
          </div>
          <div>
            <dt>Clock</dt>
            <dd>{debug.clockIso ?? "—"}</dd>
          </div>
          <div>
            <dt>Sample age</dt>
            <dd>{statusLine.ageMs == null ? "—" : `${Math.round(statusLine.ageMs)} ms`}</dd>
          </div>
        </dl>
      ) : null}
    </div>
  );
}
