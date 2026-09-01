"use client";

import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import { CircuitMap, type CircuitMapDriver } from "@/components/race-rooms/circuit-map";
import { CircuitOutline } from "@/components/race-rooms/circuit-outline";
import { getSessionState, sessionStreamUrl } from "@/lib/api";
import { formatGap, formatLapTime } from "@/lib/timing";
import { useDriverLocations } from "@/lib/use-driver-locations";
import type { DriverLocationSample, DriverRaceState, RaceState } from "@/lib/types";

import styles from "./live-command-center.module.css";

type Connection = "live" | "reconnecting" | "delayed" | "historical" | "unavailable";

type TimingRow = {
  number: number;
  name: string;
  code: string;
  position: number | null;
  change: number | null;
  gap: number | string | null;
  interval: number | string | null;
  latest: number | null;
  best: number | null;
  compound: string;
  tyreAge: number | null;
  pits: number;
};

type LiveCommandCenterProps = {
  sessionKey: string | null;
  circuitName: string;
  eventName: string;
  playbackSequence: number | null;
  /**
   * Replay position in real session time, from the backend. Persisted events
   * are sequenced per provider endpoint rather than by timestamp, so the
   * reduced state's own `last_updated_at` walks backwards; only a live session
   * can use it directly.
   */
  sessionClock: string | null;
  selectedDriver: number | null;
  onSelectDriver: (driver: number) => void;
};

function driverNumber(value: string, driver: DriverRaceState): number {
  return driver.driver_number ?? Number(value);
}

function rowsFromState(state: RaceState): TimingRow[] {
  return Object.entries(state.drivers)
    .map(([key, driver]) => {
      const number = driverNumber(key, driver);
      const name = driver.full_name ?? driver.broadcast_name ?? `Driver ${number}`;
      const surname = name.split(" ").at(-1) ?? String(number);
      const stintStart = typeof driver.stint.lap_start === "number"
        ? driver.stint.lap_start
        : typeof driver.stint.start_lap === "number"
          ? driver.stint.start_lap
          : null;
      return {
        number,
        name,
        code: surname.slice(0, 3).toUpperCase(),
        position: driver.position,
        change: driver.position_change,
        gap: driver.gap_to_leader,
        interval: driver.interval,
        latest: driver.latest_lap_duration,
        best: driver.best_lap_duration,
        compound: typeof driver.stint.compound === "string" ? driver.stint.compound.toUpperCase() : "UNKNOWN",
        tyreAge: stintStart != null && state.current_lap != null && state.current_lap >= stintStart
          ? state.current_lap - stintStart + 1
          : null,
        pits: driver.pit_stops.length,
      };
    })
    .filter((row) => Number.isFinite(row.number) && row.number > 0)
    .sort((left, right) => (left.position ?? 10_000) - (right.position ?? 10_000) || left.number - right.number);
}

/**
 * Live fixes carried on the reduced race state.
 *
 * These feed the same series the replay windows populate, so a live session
 * and a replay reach the map through one code path. A car that is not
 * transmitting reports an exact (0, 0, 0) and is not a place on the circuit.
 */
function liveLocationSamples(state: RaceState | null): DriverLocationSample[] {
  if (!state) return [];
  return Object.entries(state.drivers).flatMap(([key, driver]) => {
    const number = driverNumber(key, driver);
    const { x, y, z } = driver.location ?? {};
    const sampledAt = driver.location_updated_at;
    if (typeof x !== "number" || typeof y !== "number" || !sampledAt) return [];
    if (x === 0 && y === 0 && (z ?? 0) === 0) return [];
    return [{ driver_number: number, x, y, z: typeof z === "number" ? z : null, sample_time: sampledAt }];
  });
}

/**
 * Location diagnostics are opt-in via `?debug=location` (or `location-raw` to
 * also plot un-interpolated fixes). Nobody reaches this without asking for it,
 * so it stays out of the way of normal viewers.
 */
function useLocationDebugFlags(): { panel: boolean; raw: boolean } {
  // The query string is external state that never changes within a session,
  // and the server snapshot is empty so hydration stays deterministic.
  const debug = useSyncExternalStore(
    () => () => {},
    () => new URLSearchParams(window.location.search).get("debug") ?? "",
    () => "",
  );
  return { panel: debug.startsWith("location"), raw: debug === "location-raw" };
}

function telemetryMetric(label: string, value: number | boolean | undefined, suffix = "") {
  const display = typeof value === "boolean"
    ? value ? "ACTIVE" : "OFF"
    : typeof value === "number" ? `${Math.round(value)}${suffix}` : "—";
  return <div><span>{label}</span><b>{display}</b></div>;
}

export function LiveCommandCenter({
  sessionKey,
  circuitName,
  eventName,
  playbackSequence,
  sessionClock,
  selectedDriver,
  onSelectDriver,
}: LiveCommandCenterProps) {
  const [state, setState] = useState<RaceState | null>(null);
  const [connection, setConnection] = useState<Connection>("reconnecting");
  const [streamEpoch, setStreamEpoch] = useState(0);
  const lastSequenceRef = useRef(0);
  const { panel: debugLocation, raw: debugRawPoints } = useLocationDebugFlags();

  useEffect(() => {
    if (!sessionKey || playbackSequence == null) return;
    if (playbackSequence >= lastSequenceRef.current) return;

    // Restarting or seeking backward deliberately moves the replay state to a
    // lower sequence. Reconnect from the beginning instead of discarding the
    // valid, lower-numbered state as if it were stale network data.
    lastSequenceRef.current = 0;
    setState(null);
    setConnection("reconnecting");
    setStreamEpoch((value) => value + 1);
  }, [playbackSequence, sessionKey]);

  useEffect(() => {
    if (!sessionKey) return;
    const controller = new AbortController();
    let source: EventSource | null = null;
    let retry = 0;
    let timer: number | null = null;
    const setNewest = (next: RaceState) => {
      if (next.sequence_number < lastSequenceRef.current) return;
      lastSequenceRef.current = next.sequence_number;
      setState(next);
      setConnection(next.is_replay ? "historical" : "live");
    };
    getSessionState(sessionKey, controller.signal)
      .then(({ state: initial }) => setNewest(initial))
      .catch(() => setConnection("unavailable"));
    const connect = () => {
      source = new EventSource(sessionStreamUrl(sessionKey, lastSequenceRef.current));
      source.addEventListener("open", () => { retry = 0; setConnection("live"); });
      source.addEventListener("state", (event) => setNewest(JSON.parse((event as MessageEvent).data) as RaceState));
      source.addEventListener("stream_status", () => setConnection("delayed"));
      source.addEventListener("error", () => {
        source?.close();
        retry += 1;
        setConnection(retry > 3 ? "delayed" : "reconnecting");
        timer = window.setTimeout(connect, Math.min(8_000, 500 * 2 ** retry));
      });
    };
    connect();
    return () => {
      controller.abort();
      source?.close();
      if (timer != null) window.clearTimeout(timer);
    };
  }, [sessionKey, streamEpoch]);

  const rows = useMemo(() => state ? rowsFromState(state) : [], [state]);
  const activeDriver = selectedDriver ?? rows[0]?.number ?? null;
  const selected = activeDriver == null ? undefined : state?.drivers[String(activeDriver)];
  const activeRow = rows.find((row) => row.number === activeDriver);
  const sessionLabel = state?.current_phase ?? state?.session_type?.replaceAll("_", " ") ?? "SESSION";
  const trackStatus = String(
    state?.race_control_state.event_type ?? (state?.status === "finished" ? "COMPLETED" : "GREEN"),
  ).replaceAll("_", " ");

  const liveSamples = useMemo(() => liveLocationSamples(state), [state]);
  const locations = useDriverLocations({
    sessionKey,
    // A replay is positioned by the backend clock; a live session has no
    // replay position, so the newest applied event time is the session time.
    clockIso: sessionClock ?? (connection === "historical" ? null : state?.last_updated_at ?? null),
    liveSamples,
  });
  const mapDrivers = useMemo(
    () => new Map<number, CircuitMapDriver>(
      rows.map((row) => [row.number, { number: row.number, code: row.code, name: row.name }]),
    ),
    [rows],
  );
  const locatedLabel = locations.status === "ready"
    ? `${locations.driverNumbers.length} cars`
    : locations.status === "loading" ? "Loading" : "No positions";

  return <section className={styles.commandCenter} aria-label="Live session command center">
    <header className={styles.banner}>
      <span className={`${styles.connection} ${styles[`connection_${connection}`]}`}><i aria-hidden />{connection === "historical" ? "REPLAY" : connection.toUpperCase()}</span>
      <b>{sessionLabel}</b>
      <span>{state?.current_lap != null ? `LAP ${state.current_lap}` : "TIMING READY"}</span>
      <span className={styles.trackStatus}>{trackStatus}</span>
    </header>
    <div className={styles.grid}>
      <section className={styles.tower} aria-labelledby="timing-title">
        <div className={styles.panelHeading}><div><span>Live timing</span><h2 id="timing-title">Timing tower</h2></div><small>{state?.session_type === "QUALIFYING" ? "Best lap · gap" : "Gap · interval"}</small></div>
        {rows.length ? <div className={styles.rows} role="list">
          {rows.map((row) => <button key={row.number} className={`${styles.timingRow} ${activeDriver === row.number ? styles.selected : ""}`} type="button" onClick={() => onSelectDriver(row.number)} aria-pressed={activeDriver === row.number} aria-label={`Select ${row.name}, position ${row.position ?? "unclassified"}`}>
            <b className={styles.position}>{row.position ?? "—"}</b><span className={styles.driver}><strong>{row.code}</strong><small>{row.name}</small></span><span className={`${styles.tyre} ${styles[`tyre_${row.compound.toLowerCase()}`]}`}>{row.compound.slice(0, 1)}<i>{row.tyreAge != null ? row.tyreAge : ""}</i></span><span className={styles.gap}>{state?.session_type?.includes("QUALIFY") ? formatLapTime(row.best) : row.position === 1 ? "LEADER" : formatGap(row.gap ?? row.interval)}</span><span className={styles.delta}>{row.change ? `${row.change > 0 ? "+" : ""}${row.change}` : ""}</span>
          </button>)}
        </div> : <p className={styles.empty}>Timing will appear when this session has classified driver data.</p>}
      </section>
      <section className={styles.map} aria-labelledby="map-title">
        <div className={styles.panelHeading}><div><span>Track position</span><h2 id="map-title">Circuit map</h2></div><small>{locatedLabel}</small></div>
        <CircuitMap
          track={locations.track}
          status={locations.status}
          driverNumbers={locations.driverNumbers}
          drivers={mapDrivers}
          selectedDriver={activeDriver}
          onSelectDriver={onSelectDriver}
          sampleAt={locations.sampleAt}
          currentClockMs={locations.currentClockMs}
          debug={locations.debug}
          showDebug={debugLocation}
          showRawPoints={debugRawPoints}
          emptyVisual={<CircuitOutline circuitName={circuitName} eventName={eventName} />}
        />
      </section>
      <section className={styles.telemetry} aria-labelledby="telemetry-title">
        <div className={styles.panelHeading}><div><span>Selected driver</span><h2 id="telemetry-title">{activeRow?.name ?? "Telemetry"}</h2></div><small>{selected?.position ? `P${selected.position}` : "—"}</small></div>
        {selected?.telemetry && Object.keys(selected.telemetry).length ? <><div className={styles.speed}>{telemetryMetric("SPEED", selected.telemetry.speed, " km/h")}</div><div className={styles.telemetryGrid}>{telemetryMetric("THROTTLE", selected.telemetry.throttle, "%")}{telemetryMetric("BRAKE", selected.telemetry.brake, "%")}{telemetryMetric("GEAR", selected.telemetry.gear)}{telemetryMetric("DRS", selected.telemetry.drs)}</div></> : <div className={styles.telemetryFallback}><span className={styles.telemetryEyebrow}>Timing-only view</span><b>Car telemetry was not recorded for this session.</b><p>{activeRow ? `${activeRow.code} remains selected, so the tower and session facts stay in sync.` : "Choose a driver in the timing tower to keep the session context in focus."}</p></div>}
        {activeDriver != null && <dl className={styles.driverFacts}><div><dt>Latest</dt><dd>{formatLapTime(selected?.latest_lap_duration)}</dd></div><div><dt>Best</dt><dd>{formatLapTime(selected?.best_lap_duration)}</dd></div><div><dt>Gap</dt><dd>{formatGap(selected?.gap_to_leader)}</dd></div><div><dt>Pits</dt><dd>{selected?.pit_stops.length ?? 0}</dd></div></dl>}
      </section>
    </div>
  </section>;
}
