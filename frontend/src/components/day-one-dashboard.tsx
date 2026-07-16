// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  API_URL,
  getEngineStatus,
  getHealth,
  getSeason,
  getSessionEvents,
  getSessionState,
  sessionStreamUrl,
} from "@/lib/api";
import type {
  ComponentStatus,
  EngineStatus,
  HealthResponse,
  NormalizedRaceEvent,
  RaceMeeting,
  RaceState,
  SeasonCalendarSummary,
} from "@/lib/types";

type DashboardStatus = {
  label: string;
  status: string;
  detail: string;
};

const dateFormatter = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  year: "numeric",
});

const spaTimeFormatter = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "long",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Europe/Brussels",
  timeZoneName: "short",
});

function toneFor(status: string): "good" | "warn" | "neutral" | "loading" {
  const normalized = status.toLowerCase();
  if (["healthy", "configured", "ready", "enabled", "live", "connected", "open", "completed"].includes(normalized)) return "good";
  if (["degraded", "unavailable", "error", "missing_credentials"].includes(normalized)) return "warn";
  if (["checking", "connecting", "reconnecting"].includes(normalized)) return "loading";
  return "neutral";
}

function StatusCard({ item }: { item: DashboardStatus }) {
  const tone = toneFor(item.status);
  return (
    <article className="group rounded-2xl border border-white/8 bg-white/[0.035] p-5 transition hover:border-white/15 hover:bg-white/[0.055]">
      <div className="mb-5 flex items-center justify-between gap-4">
        <span className="text-[0.68rem] font-semibold uppercase tracking-[0.19em] text-slate-500">
          {item.label}
        </span>
        <span className={`status-dot status-dot--${tone}`} aria-hidden="true" />
      </div>
      <p className="font-mono text-sm font-semibold capitalize tracking-tight text-slate-100">
        {item.status.replaceAll("_", " ")}
      </p>
      <p className="mt-2 min-h-10 text-xs leading-5 text-slate-500">{item.detail}</p>
    </article>
  );
}

function compactStatus(
  label: string,
  component: ComponentStatus | undefined,
  fallback: string,
): DashboardStatus {
  return {
    label,
    status: component?.status ?? fallback,
    detail: component?.detail ?? "Waiting for backend telemetry.",
  };
}

function TargetRace({ race, loading }: { race?: RaceMeeting; loading: boolean }) {
  if (loading) {
    return <div className="h-64 animate-pulse rounded-3xl border border-white/8 bg-white/[0.035]" />;
  }

  if (!race) {
    return (
      <section className="rounded-3xl border border-amber-300/20 bg-amber-200/[0.04] p-8">
        <p className="text-sm text-amber-100">Spa target metadata is not available yet.</p>
      </section>
    );
  }

  return (
    <section className="target-panel relative overflow-hidden rounded-3xl border border-red-400/20 p-7 sm:p-9">
      <div className="relative z-10 grid gap-10 lg:grid-cols-[1fr_auto] lg:items-end">
        <div>
          <div className="mb-8 flex flex-wrap items-center gap-3">
            <span className="rounded-full border border-red-300/25 bg-red-400/10 px-3 py-1 font-mono text-[0.65rem] font-bold uppercase tracking-[0.2em] text-red-200">
              First live target
            </span>
            <span className="font-mono text-[0.68rem] uppercase tracking-[0.17em] text-slate-500">
              Round {String(race.round_number).padStart(2, "0")} · {race.status}
            </span>
          </div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">
            Spa-Francorchamps · Belgium
          </p>
          <h2 className="max-w-3xl text-4xl font-black tracking-[-0.045em] text-white sm:text-6xl">
            Belgian Grand Prix
          </h2>
          <p className="mt-5 max-w-2xl text-sm leading-6 text-slate-400">
            The unified race engine is pointed at Spa. Historical sessions stay replayable while
            this weekend becomes Apex Arena&apos;s first live connection target.
          </p>
        </div>
        <div className="min-w-56 border-l border-white/10 pl-6 lg:pb-1">
          <p className="text-[0.65rem] font-semibold uppercase tracking-[0.19em] text-slate-500">
            Race start · local
          </p>
          <p className="mt-3 max-w-48 text-xl font-bold leading-7 text-slate-100">
            {spaTimeFormatter.format(new Date(race.race_start))}
          </p>
          <p className="mt-3 font-mono text-xs text-red-200/75">{race.circuit_name}</p>
        </div>
      </div>
      <span className="target-number" aria-hidden="true">
        {race.round_number}
      </span>
    </section>
  );
}

function RaceRow({ race }: { race: RaceMeeting }) {
  const statusLabel =
    race.status === "completed" ? "Replay archive" : race.status === "live" ? "Live" : "Scheduled";
  return (
    <li
      className={`grid gap-3 border-t border-white/[0.065] px-1 py-5 sm:grid-cols-[4rem_1fr_9rem_7rem] sm:items-center ${race.is_target ? "race-row--target" : ""}`}
    >
      <span className="font-mono text-xs text-slate-600">
        R{String(race.round_number).padStart(2, "0")}
      </span>
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm font-semibold text-slate-200">{race.race_name}</p>
          {race.is_target ? (
            <span className="rounded-full bg-red-400/10 px-2 py-0.5 text-[0.58rem] font-bold uppercase tracking-[0.16em] text-red-200">
              Target
            </span>
          ) : null}
        </div>
        <p className="mt-1 text-xs text-slate-600">
          {race.circuit_name} · {race.country}
        </p>
      </div>
      <time className="font-mono text-xs text-slate-500" dateTime={race.race_date}>
        {dateFormatter.format(new Date(`${race.race_date}T12:00:00Z`))}
      </time>
      <span
        className={`text-[0.65rem] font-semibold uppercase tracking-[0.13em] ${
          race.status === "completed" ? "text-slate-600" : "text-emerald-300/80"
        }`}
      >
        {statusLabel}
      </span>
    </li>
  );
}

function RaceGroup({ title, races }: { title: string; races: RaceMeeting[] }) {
  return (
    <section>
      <div className="mb-3 flex items-baseline justify-between">
        <h3 className="text-xs font-bold uppercase tracking-[0.22em] text-slate-400">{title}</h3>
        <span className="font-mono text-xs text-slate-600">{races.length}</span>
      </div>
      <ol>{races.map((race) => <RaceRow key={race.id} race={race} />)}</ol>
    </section>
  );
}

type EnginePanelProps = {
  engine?: EngineStatus;
  engineError: boolean;
  selectedSession: string;
  sessionDraft: string;
  setSessionDraft: (value: string) => void;
  selectSession: () => void;
  events: NormalizedRaceEvent[];
  raceState?: RaceState;
  streamState: string;
};

function EnginePanel({
  engine,
  engineError,
  selectedSession,
  sessionDraft,
  setSessionDraft,
  selectSession,
  events,
  raceState,
  streamState,
}: EnginePanelProps) {
  const drivers = Object.entries(raceState?.drivers ?? {}).sort(
    ([, left], [, right]) => (left.position ?? 99) - (right.position ?? 99),
  );
  const mode = raceState?.is_replay ? "Replay" : engine?.live.connection_state === "CONNECTED" ? "Live" : "Standby";

  return (
    <section aria-labelledby="engine-heading" className="mb-20 rounded-3xl border border-white/8 bg-[#0a0d12]/90 p-6 sm:p-9">
      <div className="grid gap-8 border-b border-white/8 pb-8 lg:grid-cols-[1fr_auto] lg:items-end">
        <div>
          <p className="section-kicker">Unified race engine</p>
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <h2 id="engine-heading" className="text-3xl font-black tracking-[-0.035em] text-white">
              Race signal
            </h2>
            <span className={`rounded-full border px-2.5 py-1 font-mono text-[0.62rem] font-bold uppercase tracking-[0.16em] ${mode === "Live" ? "border-emerald-300/25 bg-emerald-300/10 text-emerald-200" : "border-sky-300/20 bg-sky-300/8 text-sky-200"}`}>
              {mode}
            </span>
          </div>
          <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-500">
            Live MQTT and historical OpenF1 records share one idempotent, ordered state pipeline.
          </p>
        </div>
        <form
          className="flex max-w-md gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            selectSession();
          }}
        >
          <label className="sr-only" htmlFor="session-key">OpenF1 session key</label>
          <input
            id="session-key"
            value={sessionDraft}
            onChange={(event) => setSessionDraft(event.target.value)}
            placeholder="OpenF1 session key"
            className="min-w-0 flex-1 rounded-xl border border-white/10 bg-black/20 px-4 py-2.5 font-mono text-xs text-slate-200 outline-none transition placeholder:text-slate-700 focus:border-red-300/40"
          />
          <button type="submit" className="rounded-xl border border-white/10 px-4 py-2 text-[0.64rem] font-bold uppercase tracking-[0.14em] text-slate-300 transition hover:border-white/25 hover:text-white">
            Connect
          </button>
        </form>
      </div>

      {engineError ? (
        <div className="mt-6 rounded-xl border border-amber-300/20 bg-amber-200/[0.04] p-4 text-sm text-amber-100">
          Engine telemetry is unavailable. The season dashboard remains in degraded mode.
        </div>
      ) : null}

      <div className="grid gap-3 py-7 sm:grid-cols-2 lg:grid-cols-6">
        {[
          ["Raw", engine?.raw_event_count],
          ["Normalized", engine?.normalized_event_count],
          ["Sequence", engine?.latest_sequence_number],
          ["Snapshots", engine?.snapshot_count],
          ["Buffer", engine?.ordering_buffer_pending],
          ["Stream", streamState.replaceAll("_", " ")],
        ].map(([label, value]) => (
          <div key={String(label)} className="rounded-xl border border-white/[0.065] bg-white/[0.025] p-4">
            <p className="font-mono text-lg font-bold capitalize text-slate-100">{value ?? "—"}</p>
            <p className="mt-1 text-[0.6rem] font-semibold uppercase tracking-[0.16em] text-slate-600">{label}</p>
          </div>
        ))}
      </div>

      {engine?.live.degraded_reason ? (
        <p className="mb-7 rounded-xl border border-amber-300/20 bg-amber-200/[0.04] px-4 py-3 text-xs text-amber-100">
          Live feed degraded: {engine.live.degraded_reason}. Historical replay remains available.
        </p>
      ) : null}

      <div className="grid gap-8 xl:grid-cols-[1.15fr_0.85fr]">
        <div>
          <div className="mb-4 flex items-center justify-between gap-4">
            <h3 className="text-xs font-bold uppercase tracking-[0.2em] text-slate-400">Latest events</h3>
            <span className="font-mono text-[0.65rem] text-slate-600">{selectedSession || "NO SESSION"}</span>
          </div>
          <ol className="divide-y divide-white/[0.06] border-y border-white/[0.06]">
            {events.length ? events.slice(0, 10).map((event) => (
              <li key={event.id} className="grid grid-cols-[3.3rem_1fr_auto] items-center gap-3 py-3">
                <span className="font-mono text-[0.65rem] text-slate-600">#{event.sequence_number}</span>
                <div>
                  <p className="text-xs font-semibold text-slate-300">{event.event_type.replaceAll("_", " ")}</p>
                  <p className="mt-1 font-mono text-[0.6rem] text-slate-650">
                    {event.driver_numbers.length ? `DRIVER ${event.driver_numbers.join(", ")}` : "SESSION"}
                    {event.lap_number ? ` · LAP ${event.lap_number}` : ""}
                  </p>
                </div>
                <span className="text-[0.58rem] font-bold uppercase tracking-[0.12em] text-slate-600">{event.is_replay ? "Replay" : "Live"}</span>
              </li>
            )) : (
              <li className="py-12 text-center text-xs text-slate-600">Select a session to inspect its event stream.</li>
            )}
          </ol>
        </div>

        <div>
          <div className="mb-4 flex items-center justify-between gap-4">
            <h3 className="text-xs font-bold uppercase tracking-[0.2em] text-slate-400">Current state</h3>
            <span className="font-mono text-[0.65rem] uppercase text-slate-600">{raceState?.status ?? "unknown"}</span>
          </div>
          <div className="rounded-2xl border border-white/[0.07] bg-black/15 p-5">
            <div className="grid grid-cols-3 gap-4 border-b border-white/[0.06] pb-5">
              <div><p className="font-mono text-xl font-bold text-white">{raceState?.current_lap ?? "—"}</p><p className="mt-1 text-[0.58rem] uppercase tracking-[0.14em] text-slate-600">Lap</p></div>
              <div><p className="font-mono text-xl font-bold text-white">{raceState?.sequence_number ?? "—"}</p><p className="mt-1 text-[0.58rem] uppercase tracking-[0.14em] text-slate-600">Sequence</p></div>
              <div><p className="font-mono text-xl font-bold text-white">{drivers.length || "—"}</p><p className="mt-1 text-[0.58rem] uppercase tracking-[0.14em] text-slate-600">Drivers</p></div>
            </div>
            <ol className="mt-4 space-y-2">
              {drivers.slice(0, 6).map(([number, driver]) => (
                <li key={number} className="grid grid-cols-[2rem_1fr_auto] items-center gap-3 text-xs">
                  <span className="font-mono font-bold text-slate-300">{driver.position ?? "—"}</span>
                  <span className="text-slate-500">Driver {number}</span>
                  <span className="font-mono text-[0.65rem] text-slate-600">{driver.gap_to_leader ?? driver.interval ?? "—"}</span>
                </li>
              ))}
              {!drivers.length ? <li className="py-7 text-center text-xs text-slate-600">No state samples yet.</li> : null}
            </ol>
          </div>
          <p className="mt-3 font-mono text-[0.62rem] leading-5 text-slate-700">
            {raceState?.last_updated_at ? `UPDATED ${new Date(raceState.last_updated_at).toLocaleTimeString()}` : "AWAITING STATE"}
            {engine?.latest_ingestion ? ` · INGESTION ${engine.latest_ingestion.status.toUpperCase()}` : ` · HISTORICAL ${engine?.historical_ingestion_enabled ? "ENABLED" : "DISABLED"}`}
          </p>
        </div>
      </div>
    </section>
  );
}

export function ApexArenaDashboard() {
  const [health, setHealth] = useState<HealthResponse>();
  const [season, setSeason] = useState<SeasonCalendarSummary>();
  const [engine, setEngine] = useState<EngineStatus>();
  const [healthError, setHealthError] = useState(false);
  const [seasonError, setSeasonError] = useState(false);
  const [engineError, setEngineError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [sessionDraft, setSessionDraft] = useState("");
  const [selectedSession, setSelectedSession] = useState("");
  const [events, setEvents] = useState<NormalizedRaceEvent[]>([]);
  const [raceState, setRaceState] = useState<RaceState>();
  const [streamState, setStreamState] = useState("idle");

  const loadData = useCallback(async () => {
    const [healthResult, seasonResult, engineResult] = await Promise.allSettled([
      getHealth(),
      getSeason(),
      getEngineStatus(),
    ]);

    if (healthResult.status === "fulfilled") {
      setHealth(healthResult.value);
      setHealthError(false);
    } else {
      setHealthError(true);
    }

    if (seasonResult.status === "fulfilled") {
      setSeason(seasonResult.value);
      setSeasonError(false);
    } else {
      setSeasonError(true);
    }
    if (engineResult.status === "fulfilled") {
      setEngine(engineResult.value);
      setEngineError(false);
      if (engineResult.value.current_session_key) {
        setSelectedSession((current) => current || engineResult.value.current_session_key || "");
        setSessionDraft((current) => current || engineResult.value.current_session_key || "");
        setStreamState((current) => current === "idle" ? "connecting" : current);
      }
    } else {
      setEngineError(true);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void Promise.allSettled([
      getHealth(controller.signal),
      getSeason(controller.signal),
      getEngineStatus(controller.signal),
    ]).then(([healthResult, seasonResult, engineResult]) => {
      if (controller.signal.aborted) return;
      if (healthResult.status === "fulfilled") {
        setHealth(healthResult.value);
        setHealthError(false);
      } else {
        setHealthError(true);
      }
      if (seasonResult.status === "fulfilled") {
        setSeason(seasonResult.value);
        setSeasonError(false);
      } else {
        setSeasonError(true);
      }
      if (engineResult.status === "fulfilled") {
        setEngine(engineResult.value);
        setEngineError(false);
        if (engineResult.value.current_session_key) {
          setSelectedSession((current) => current || engineResult.value.current_session_key || "");
          setSessionDraft((current) => current || engineResult.value.current_session_key || "");
          setStreamState((current) => current === "idle" ? "connecting" : current);
        }
      } else {
        setEngineError(true);
      }
      setLoading(false);
    });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!selectedSession) return;

    const controller = new AbortController();
    let source: EventSource | undefined;
    let cancelled = false;
    void Promise.all([
      getSessionEvents(selectedSession, controller.signal),
      getSessionState(selectedSession, controller.signal),
    ]).then(([eventResponse, stateResponse]) => {
      if (cancelled) return;
      const initialEvents = [...eventResponse.events].reverse();
      setEvents(initialEvents.slice(0, 20));
      setRaceState(stateResponse.state);
      const lastSequence = eventResponse.events.at(-1)?.sequence_number ?? 0;
      source = new EventSource(sessionStreamUrl(selectedSession, lastSequence));
      source.onopen = () => setStreamState("open");
      source.onerror = () => setStreamState("reconnecting");
      source.addEventListener("event", (message) => {
        const next = JSON.parse((message as MessageEvent<string>).data) as NormalizedRaceEvent;
        setEvents((current) => [next, ...current.filter((item) => item.id !== next.id)].slice(0, 20));
      });
      source.addEventListener("state", (message) => {
        setRaceState(JSON.parse((message as MessageEvent<string>).data) as RaceState);
      });
      source.addEventListener("connection_status", (message) => {
        const live = JSON.parse((message as MessageEvent<string>).data) as EngineStatus["live"];
        setEngine((current) => current ? { ...current, live: { ...current.live, ...live } } : current);
      });
      source.addEventListener("stream_status", () => setStreamState("degraded"));
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) {
        console.warn("Race session data unavailable", error instanceof Error ? error.name : "Error");
        setStreamState("degraded");
      }
    });

    return () => {
      cancelled = true;
      controller.abort();
      source?.close();
    };
  }, [selectedSession]);

  const cards = useMemo<DashboardStatus[]>(() => {
    const fallback = loading ? "checking" : "unavailable";
    return [
      {
        label: "Backend health",
        status: health?.status ?? fallback,
        detail: healthError ? `No response from ${API_URL}` : "FastAPI control plane is responding.",
      },
      {
        label: "Race engine",
        status: engine?.status ?? fallback,
        detail: engineError ? "Engine telemetry is unavailable." : "Unified live and replay pipeline.",
      },
      compactStatus("PostgreSQL", health?.database, fallback),
      compactStatus("Redis", health?.redis, fallback),
      compactStatus("OpenF1 REST", health?.openf1_rest, fallback),
      compactStatus("OpenF1 live auth", health?.openf1_live, fallback),
      compactStatus("Jolpica", health?.jolpica, fallback),
      compactStatus("AI systems", health?.ai, fallback),
    ];
  }, [engine?.status, engineError, health, healthError, loading]);

  const targetRace = season?.races.find((race) => race.is_target);
  const completed = season?.races.filter((race) => race.status === "completed") ?? [];
  const onDeck = season?.races.filter((race) => race.status !== "completed") ?? [];

  return (
    <main className="track-grid min-h-screen overflow-hidden">
      <div className="mx-auto max-w-[90rem] px-5 pb-12 pt-6 sm:px-8 lg:px-12">
        <header className="flex items-center justify-between border-b border-white/8 pb-5">
          <a href="#top" className="flex items-center gap-3" aria-label="Apex Arena home">
            <span className="brand-mark" aria-hidden="true" />
            <span className="text-sm font-black uppercase tracking-[-0.02em] text-white">
              Apex <span className="text-red-400">Arena</span>
            </span>
          </a>
          <div className="flex items-center gap-3">
            <span className="hidden font-mono text-[0.62rem] uppercase tracking-[0.17em] text-slate-600 sm:inline">
              2026 season · Day 02
            </span>
            <button
              type="button"
              onClick={() => {
                setLoading(true);
                void loadData();
              }}
              disabled={loading}
              className="rounded-full border border-white/10 px-4 py-2 text-[0.65rem] font-bold uppercase tracking-[0.16em] text-slate-300 transition hover:border-white/25 hover:text-white disabled:cursor-wait disabled:opacity-50"
            >
              {loading ? "Checking" : "Refresh"}
            </button>
          </div>
        </header>

        <div id="top" className="grid gap-12 pb-16 pt-16 lg:grid-cols-[1fr_auto] lg:items-end">
          <div>
            <p className="mb-5 font-mono text-[0.68rem] font-bold uppercase tracking-[0.25em] text-red-300">
              Unified race engine / Understand and stream the race
            </p>
            <h1 className="max-w-5xl text-5xl font-black leading-[0.96] tracking-[-0.055em] text-white sm:text-7xl lg:text-[6.4rem]">
              Race control,
              <br />
              <span className="text-slate-500">connected.</span>
            </h1>
          </div>
          <div className="max-w-sm border-l border-red-400/30 pl-5 text-sm leading-6 text-slate-500">
            A public, 2026-only Formula racing simulation foundation. Live and replay data now
            converge into one observable race state.
          </div>
        </div>

        <section aria-labelledby="systems-heading" className="mb-16">
          <div className="mb-5 flex items-end justify-between gap-6">
            <div>
              <p className="section-kicker">System telemetry</p>
              <h2 id="systems-heading" className="mt-2 text-2xl font-bold tracking-tight text-white">
                Engine foundation status
              </h2>
            </div>
            <p className="hidden font-mono text-[0.65rem] text-slate-600 sm:block">
              {health?.checked_at
                ? `CHECKED ${new Date(health.checked_at).toLocaleTimeString()}`
                : "AWAITING BACKEND"}
            </p>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {cards.map((item) => <StatusCard key={item.label} item={item} />)}
          </div>
        </section>

        <EnginePanel
          engine={engine}
          engineError={engineError}
          selectedSession={selectedSession}
          sessionDraft={sessionDraft}
          setSessionDraft={setSessionDraft}
          selectSession={() => {
            const nextSession = sessionDraft.trim();
            if (!nextSession) return;
            setEvents([]);
            setRaceState(undefined);
            setStreamState("connecting");
            setSelectedSession(nextSession);
          }}
          events={events}
          raceState={raceState}
          streamState={streamState}
        />

        <section aria-labelledby="target-heading" className="mb-20">
          <p className="section-kicker mb-5" id="target-heading">
            Target weekend
          </p>
          <TargetRace race={targetRace} loading={loading && !season} />
        </section>

        <section aria-labelledby="calendar-heading" className="rounded-3xl border border-white/8 bg-[#0a0d12]/80 p-6 sm:p-9">
          <div className="grid gap-8 border-b border-white/8 pb-9 md:grid-cols-[1fr_auto] md:items-end">
            <div>
              <p className="section-kicker">Jolpica calendar</p>
              <h2 id="calendar-heading" className="mt-3 text-3xl font-black tracking-[-0.035em] text-white">
                The 2026 season
              </h2>
              <p className="mt-3 max-w-xl text-sm leading-6 text-slate-500">
                Completed weekends are replay/archive candidates. Future rounds remain scheduled;
                Spa is promoted as the first live room target.
              </p>
            </div>
            <div className="flex gap-8">
              {[
                ["Races", season?.total_races],
                ["Archived", season?.completed_races],
                ["On deck", season ? season.upcoming_races + season.live_races : undefined],
              ].map(([label, value]) => (
                <div key={String(label)}>
                  <p className="font-mono text-2xl font-bold text-slate-100">{value ?? "—"}</p>
                  <p className="mt-1 text-[0.62rem] font-semibold uppercase tracking-[0.16em] text-slate-600">
                    {label}
                  </p>
                </div>
              ))}
            </div>
          </div>

          {seasonError ? (
            <div className="py-16 text-center">
              <p className="text-sm font-semibold text-amber-100">Calendar provider unavailable</p>
              <p className="mt-2 text-xs text-slate-600">The backend remains usable in degraded mode.</p>
            </div>
          ) : loading && !season ? (
            <div className="space-y-3 py-10">
              {[1, 2, 3, 4].map((item) => (
                <div key={item} className="h-16 animate-pulse rounded-xl bg-white/[0.035]" />
              ))}
            </div>
          ) : (
            <div className="grid gap-12 pt-10 xl:grid-cols-2 xl:gap-16">
              <RaceGroup title="Replay archive" races={completed} />
              <RaceGroup title="Upcoming & live" races={onDeck} />
            </div>
          )}
        </section>

        <footer className="mt-10 flex flex-col gap-3 border-t border-white/8 pt-6 text-[0.66rem] leading-5 text-slate-700 sm:flex-row sm:items-center sm:justify-between">
          <p>Unofficial fan project · Not affiliated with Formula 1, the FIA, OpenF1, or Jolpica.</p>
          <p className="font-mono uppercase tracking-[0.12em]">AGPL-3.0-only · Apex Arena v0.1</p>
        </footer>
      </div>
    </main>
  );
}
