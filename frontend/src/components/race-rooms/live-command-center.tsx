"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { CircuitOutline } from "@/components/race-rooms/circuit-outline";
import { getSessionState, sessionStreamUrl } from "@/lib/api";
import { formatGap, formatLapTime } from "@/lib/timing";
import type { DriverRaceState, RaceState } from "@/lib/types";

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

function locationPoints(state: RaceState, rows: TimingRow[]) {
  const raw = rows.flatMap((row) => {
    const location = state.drivers[String(row.number)]?.location;
    return typeof location?.x === "number" && typeof location.y === "number"
      ? [{ row, x: location.x, y: location.y }]
      : [];
  });
  if (raw.length < 2) return [];
  const minX = Math.min(...raw.map((point) => point.x));
  const maxX = Math.max(...raw.map((point) => point.x));
  const minY = Math.min(...raw.map((point) => point.y));
  const maxY = Math.max(...raw.map((point) => point.y));
  const width = Math.max(maxX - minX, 1);
  const height = Math.max(maxY - minY, 1);
  return raw.map((point) => ({
    ...point,
    x: 12 + ((point.x - minX) / width) * 76,
    y: 12 + ((point.y - minY) / height) * 76,
  }));
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
  selectedDriver,
  onSelectDriver,
}: LiveCommandCenterProps) {
  const [state, setState] = useState<RaceState | null>(null);
  const [connection, setConnection] = useState<Connection>("reconnecting");
  const [streamEpoch, setStreamEpoch] = useState(0);
  const lastSequenceRef = useRef(0);

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
  const locations = useMemo(() => state ? locationPoints(state, rows) : [], [rows, state]);
  const sessionLabel = state?.current_phase ?? state?.session_type?.replaceAll("_", " ") ?? "SESSION";
  const trackStatus = String(
    state?.race_control_state.event_type ?? (state?.status === "finished" ? "COMPLETED" : "GREEN"),
  ).replaceAll("_", " ");
  const locationUnavailableCopy = connection === "historical"
    ? "This replay was ingested without the optional GPS feed. The circuit reference remains available."
    : "Live location samples have not arrived yet. The circuit reference remains available while timing continues.";

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
        <div className={styles.panelHeading}><div><span>Track position</span><h2 id="map-title">Circuit map</h2></div><small>{locations.length ? `${locations.length} cars` : "Circuit reference"}</small></div>
        {locations.length ? <svg className={styles.track} viewBox="0 0 100 100" role="img" aria-label="Live driver position map"><rect x="2" y="2" width="96" height="96" rx="9" className={styles.trackField} />{locations.map((point) => <g key={point.row.number} className={activeDriver === point.row.number ? styles.markerSelected : ""} onClick={() => onSelectDriver(point.row.number)} tabIndex={0} role="button" aria-label={`Select ${point.row.name}`} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") onSelectDriver(point.row.number); }}><circle cx={point.x} cy={point.y} r={activeDriver === point.row.number ? 5.5 : 3.5} /><text x={point.x} y={point.y + 1}>{point.row.code.slice(0, 2)}</text></g>)}</svg> : <div className={styles.mapFallback}><CircuitOutline circuitName={circuitName} eventName={eventName} /><div><b>Live car positions unavailable</b><p>{locationUnavailableCopy}</p></div></div>}
      </section>
      <section className={styles.telemetry} aria-labelledby="telemetry-title">
        <div className={styles.panelHeading}><div><span>Selected driver</span><h2 id="telemetry-title">{activeRow?.name ?? "Telemetry"}</h2></div><small>{selected?.position ? `P${selected.position}` : "—"}</small></div>
        {selected?.telemetry && Object.keys(selected.telemetry).length ? <><div className={styles.speed}>{telemetryMetric("SPEED", selected.telemetry.speed, " km/h")}</div><div className={styles.telemetryGrid}>{telemetryMetric("THROTTLE", selected.telemetry.throttle, "%")}{telemetryMetric("BRAKE", selected.telemetry.brake, "%")}{telemetryMetric("GEAR", selected.telemetry.gear)}{telemetryMetric("DRS", selected.telemetry.drs)}</div></> : <div className={styles.telemetryFallback}><span className={styles.telemetryEyebrow}>Timing-only view</span><b>Car telemetry was not recorded for this session.</b><p>{activeRow ? `${activeRow.code} remains selected, so the tower and session facts stay in sync.` : "Choose a driver in the timing tower to keep the session context in focus."}</p></div>}
        {activeDriver != null && <dl className={styles.driverFacts}><div><dt>Latest</dt><dd>{formatLapTime(selected?.latest_lap_duration)}</dd></div><div><dt>Best</dt><dd>{formatLapTime(selected?.best_lap_duration)}</dd></div><div><dt>Gap</dt><dd>{formatGap(selected?.gap_to_leader)}</dd></div><div><dt>Pits</dt><dd>{selected?.pit_stops.length ?? 0}</dd></div></dl>}
      </section>
    </div>
  </section>;
}
