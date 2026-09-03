"use client";

import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import { CircuitMap, type CircuitMapDriver } from "@/components/race-rooms/circuit-map";
import { CircuitOutline } from "@/components/race-rooms/circuit-outline";
import { BattleRail } from "@/components/race-rooms/battle-rail";
import { RaceEventFeed, RecentChanges } from "@/components/race-rooms/race-event-feed";
import { RaceRoomModeToggle, useRaceRoomMode } from "@/components/race-rooms/race-room-mode-toggle";
import { SelectedDriverContext } from "@/components/race-rooms/selected-driver-context";
import { getSessionEvents, getSessionState, sessionStreamUrl } from "@/lib/api";
import { formatGap, formatLapTime } from "@/lib/timing";
import { useDriverLocations } from "@/lib/use-driver-locations";
import type {
  BattleState,
  DriverBattleContext,
  DriverLocationSample,
  DriverRaceState,
  DriverTimingState,
  NormalizedRaceEvent,
  RaceState,
  SessionIntelligenceState,
  TyreCompound,
} from "@/lib/types";

import styles from "./live-command-center.module.css";

type Connection = "live" | "reconnecting" | "delayed" | "historical" | "unavailable";
const EMPTY_BATTLES: BattleState[] = [];

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
  initialIntelligence?: SessionIntelligenceState;
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

function mergeEvents(
  current: NormalizedRaceEvent[],
  incoming: NormalizedRaceEvent[],
): NormalizedRaceEvent[] {
  const indexed = new Map(current.map((event) => [event.id, event]));
  for (const event of incoming) indexed.set(event.id, event);
  return [...indexed.values()]
    .sort((left, right) => left.sequence_number - right.sequence_number)
    .slice(-250);
}

function numeric(value: number | string | null): number | null {
  if (value == null || typeof value === "boolean") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function battleContext(
  row: TimingRow,
  rows: TimingRow[],
  battles: BattleState[],
): DriverBattleContext {
  const index = rows.findIndex((candidate) => candidate.number === row.number);
  const ahead = index > 0 ? rows[index - 1] : undefined;
  const behind = index >= 0 ? rows[index + 1] : undefined;
  const battle = battles.find((candidate) => (
    candidate.lead_driver_number === row.number || candidate.chasing_driver_number === row.number
  ));
  if (!battle) {
    return {
      driver_number: row.number,
      ahead_driver_number: ahead?.number ?? null,
      ahead_interval_seconds: numeric(row.interval),
      behind_driver_number: behind?.number ?? null,
      behind_interval_seconds: numeric(behind?.interval ?? null),
      status: row.position == null ? "UNAVAILABLE" : "CLEAR_AIR",
      battle_id: null,
    };
  }
  const chasing = battle.chasing_driver_number === row.number;
  return {
    driver_number: row.number,
    ahead_driver_number: chasing ? battle.lead_driver_number : ahead?.number ?? null,
    ahead_interval_seconds: chasing ? battle.interval_seconds : numeric(row.interval),
    behind_driver_number: chasing ? behind?.number ?? null : battle.chasing_driver_number,
    behind_interval_seconds: chasing ? numeric(behind?.interval ?? null) : battle.interval_seconds,
    status: chasing
      ? battle.trend === "CLOSING" ? "CLOSING" : "BATTLING"
      : "UNDER_PRESSURE",
    battle_id: battle.id,
  };
}

function timingRows(rows: TimingRow[], battles: BattleState[]): DriverTimingState[] {
  return rows.map((row) => ({
    driver_number: row.number,
    name: row.name,
    abbreviation: row.code,
    team_name: null,
    position: row.position,
    position_change: row.change,
    gap_to_leader: row.gap,
    interval: row.interval,
    latest_lap: row.latest,
    best_lap: row.best,
    tyre_compound: row.compound as TyreCompound,
    tyre_age_laps: row.tyreAge,
    pit_stop_count: row.pits,
    is_fastest: false,
    is_personal_best: row.latest != null && row.latest === row.best,
    status: "RUNNING",
    battle_context: battleContext(row, rows, battles),
  }));
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
  initialIntelligence,
}: LiveCommandCenterProps) {
  const [state, setState] = useState<RaceState | null>(null);
  const [connection, setConnection] = useState<Connection>("reconnecting");
  const [streamEpoch, setStreamEpoch] = useState(0);
  const [events, setEvents] = useState<NormalizedRaceEvent[]>(
    initialIntelligence?.recent_events ?? [],
  );
  const [hasMoreEvents, setHasMoreEvents] = useState(false);
  const [loadingMoreEvents, setLoadingMoreEvents] = useState(false);
  const lastSequenceRef = useRef(0);
  const eventPageCursorRef = useRef(0);
  const { mode, setMode } = useRaceRoomMode();
  const { panel: debugLocation, raw: debugRawPoints } = useLocationDebugFlags();

  useEffect(() => {
    if (!sessionKey || playbackSequence == null) return;
    const hasAppliedState = lastSequenceRef.current > 0;
    const jumpedBackward = playbackSequence < lastSequenceRef.current;
    const jumpedForward = hasAppliedState && playbackSequence > lastSequenceRef.current + 1;
    if (!jumpedBackward && !jumpedForward) return;

    // Replays can jump when a control seeks across the timeline. Reconnect
    // from a clean cursor so the next state fetch bounds the event feed again.
    lastSequenceRef.current = 0;
    eventPageCursorRef.current = 0;
    setState(null);
    setEvents([]);
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
      .then(({ state: initial }) => {
        setNewest(initial);
        return getSessionEvents(
          sessionKey,
          {
            beforeSequenceNumber: initial.is_replay ? initial.sequence_number : undefined,
            limit: 100,
            minimumImportance: "NORMAL",
          },
          controller.signal,
        ).then((response) => {
          eventPageCursorRef.current = response.events.at(-1)?.sequence_number ?? 0;
          setEvents((current) => mergeEvents(current, response.events));
          setHasMoreEvents(response.count === 100);
        }).catch(() => undefined);
      })
      .catch(() => setConnection("unavailable"));
    const connect = () => {
      source = new EventSource(sessionStreamUrl(sessionKey, lastSequenceRef.current));
      source.addEventListener("open", () => { retry = 0; setConnection("live"); });
      source.addEventListener("state", (event) => setNewest(JSON.parse((event as MessageEvent).data) as RaceState));
      source.addEventListener("event", (event) => {
        const next = JSON.parse((event as MessageEvent).data) as NormalizedRaceEvent;
        if (next.importance_level !== "LOW") {
          setEvents((current) => mergeEvents(current, [next]));
        }
      });
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
  const battles = useMemo(
    () => state?.current_battles ?? initialIntelligence?.current_battles ?? EMPTY_BATTLES,
    [initialIntelligence?.current_battles, state?.current_battles],
  );
  const timing = useMemo(() => timingRows(rows, battles), [battles, rows]);
  const activeDriver = selectedDriver ?? rows[0]?.number ?? null;
  const selected = activeDriver == null ? undefined : state?.drivers[String(activeDriver)];
  const activeRow = rows.find((row) => row.number === activeDriver);
  const selectedTiming = timing.find((row) => row.driver_number === activeDriver);
  const replaySequence = state?.is_replay ? state.sequence_number : null;
  const displayedEvents = useMemo(
    () => replaySequence == null
      ? events
      : events.filter((event) => event.sequence_number <= replaySequence),
    [events, replaySequence],
  );
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
  const battleDrivers = useMemo(
    () => new Set(battles.flatMap((battle) => [
      battle.lead_driver_number,
      battle.chasing_driver_number,
    ])),
    [battles],
  );
  const nameForDriver = (number: number) => (
    rows.find((row) => row.number === number)?.name ?? `Car ${number}`
  );
  const loadMoreEvents = async () => {
    if (!sessionKey || loadingMoreEvents) return;
    setLoadingMoreEvents(true);
    try {
      const after = eventPageCursorRef.current;
      const response = await getSessionEvents(sessionKey, {
        afterSequenceNumber: after,
        beforeSequenceNumber: replaySequence ?? undefined,
        limit: 100,
        minimumImportance: "NORMAL",
      });
      eventPageCursorRef.current = response.events.at(-1)?.sequence_number ?? after;
      setEvents((current) => mergeEvents(current, response.events));
      setHasMoreEvents(response.count === 100);
    } finally {
      setLoadingMoreEvents(false);
    }
  };
  const locatedLabel = locations.status === "ready"
    ? `${locations.driverNumbers.length} cars`
    : locations.status === "loading" ? "Loading" : "No positions";

  return <>
    <section className={styles.commandCenter} aria-label="Live session command center" data-mode={mode.toLowerCase()}>
    <header className={styles.banner}>
      <span className={`${styles.connection} ${styles[`connection_${connection}`]}`}><i aria-hidden />{connection === "historical" ? "REPLAY" : connection.toUpperCase()}</span>
      <b>{sessionLabel}</b>
      <span>{state?.current_lap != null ? `LAP ${state.current_lap}` : "TIMING READY"}</span>
      <span className={styles.trackStatus}>{trackStatus}</span>
      <RaceRoomModeToggle mode={mode} onModeChange={setMode} />
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
          battleDrivers={battleDrivers}
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
        <SelectedDriverContext driverNumber={activeDriver} timing={timing} context={selectedTiming?.battle_context ?? null} mode={mode} />
        {mode === "ANALYST" ? selected?.telemetry && Object.keys(selected.telemetry).length ? <><div className={styles.speed}>{telemetryMetric("SPEED", selected.telemetry.speed, " km/h")}</div><div className={styles.telemetryGrid}>{telemetryMetric("THROTTLE", selected.telemetry.throttle, "%")}{telemetryMetric("BRAKE", selected.telemetry.brake, "%")}{telemetryMetric("GEAR", selected.telemetry.gear)}{telemetryMetric("DRS", selected.telemetry.drs)}</div></> : <div className={styles.telemetryFallback}><span className={styles.telemetryEyebrow}>Timing-only view</span><b>Car telemetry was not recorded for this session.</b><p>{activeRow ? `${activeRow.code} remains selected, so the tower and session facts stay in sync.` : "Choose a driver in the timing tower to keep the session context in focus."}</p></div> : null}
        {activeDriver != null && <dl className={styles.driverFacts}><div><dt>Latest</dt><dd>{formatLapTime(selected?.latest_lap_duration)}</dd></div><div><dt>Best</dt><dd>{formatLapTime(selected?.best_lap_duration)}</dd></div><div><dt>Gap</dt><dd>{formatGap(selected?.gap_to_leader)}</dd></div><div><dt>Pits</dt><dd>{selected?.pit_stops.length ?? 0}</dd></div></dl>}
      </section>
    </div>
    </section>
    <BattleRail battles={battles} drivers={state?.drivers ?? {}} currentLap={state?.current_lap ?? null} selectedDriver={activeDriver} mode={mode} onSelectDriver={onSelectDriver} />
    <RecentChanges events={displayedEvents.slice(-5)} driverName={nameForDriver} />
    <RaceEventFeed events={displayedEvents} selectedDriver={activeDriver} driverName={nameForDriver} onLoadMore={loadMoreEvents} hasMore={hasMoreEvents} loadingMore={loadingMoreEvents} />
  </>;
}
