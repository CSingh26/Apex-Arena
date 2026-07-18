// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { ThemeToggle } from "@/components/race-rooms/theme-toggle";
import { getRaceRooms, getSeason } from "@/lib/api";
import type { RaceMeeting, RaceRoom, RaceWeekendSession } from "@/lib/types";

function raceDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" }).format(new Date(value));
}

function RoomCard({ room, featured = false }: { room: RaceRoom; featured?: boolean }) {
  const available = room.source_availability !== "unavailable";
  return <Link className={`room-card ${featured ? "room-card--featured" : ""}`} data-room-slug={room.slug} href={`/race-rooms/${room.slug}`} aria-label={`Enter ${room.race_name} Race Room`}>
    <div className="room-card__meta"><span className={`status status--${room.status}`}>{room.status}</span><span>{room.mode}</span><span>{raceDate(room.scheduled_start)}</span></div>
    <div className="room-card__copy"><p className="room-card__round">Round {room.round_number ?? "—"} · {room.session_type}</p><h3>{room.race_name}</h3><p>{room.circuit_name} · {room.country_code ?? room.country}</p></div>
    <div className="room-card__stats"><span><b>{room.agent_count}</b> agents</span><span><b>{room.message_count}</b> messages</span><span>{room.status === "completed" ? <b>Final</b> : <><b>{room.current_lap ?? "—"}</b> / {room.total_laps ?? "—"} laps</>}</span><span className={!available ? "coverage-unavailable" : ""}>{room.source_availability.replaceAll("_", " ")}</span><span>Active {room.last_event_at ? raceDate(room.last_event_at) : "not yet"}</span></div>
    {room.is_development && <strong className="dev-label"><span aria-hidden>◆</span> Deterministic validation data</strong>}
    <span className="room-card__enter">{available ? "Enter conversation" : "View room status"} <b aria-hidden>→</b></span>
  </Link>;
}

type UpcomingSession = RaceWeekendSession & Pick<RaceMeeting, "race_name" | "circuit_name" | "country" | "round_number">;

function sessionCountdown(milliseconds: number) {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const days = Math.floor(totalSeconds / 86400);
  const hours = Math.floor((totalSeconds % 86400) / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return { days, hours, minutes, seconds };
}

function NextSession({ session, now }: { session: UpcomingSession; now: number }) {
  const start = new Date(session.starts_at);
  const remaining = start.getTime() - now;
  const countdown = sessionCountdown(remaining);
  const live = remaining <= 0 && remaining > -3 * 60 * 60 * 1000;
  const date = new Intl.DateTimeFormat(undefined, { weekday: "long", day: "numeric", month: "long", hour: "numeric", minute: "2-digit", timeZoneName: "short" }).format(start);
  return <section className="next-session" aria-labelledby="next-session-title">
    <div className="next-session__eyebrow"><span className={live ? "live-pulse" : "signal-pulse"} />{live ? "Live now" : "Next live session"}</div>
    <div className="next-session__layout">
      <div className="next-session__copy">
        <p>Round {session.round_number} · {session.country}</p>
        <h2 id="next-session-title">{session.name}</h2>
        <h3>{session.race_name}</h3>
        <span>{session.circuit_name}</span>
      </div>
      {live ? <div className="next-session__live"><strong>On air</strong><span>The session is underway</span></div> : <div className="countdown" aria-label={`Countdown to ${session.name}`}>
        {Object.entries(countdown).map(([unit, value]) => <div key={unit}><strong>{String(value).padStart(2, "0")}</strong><span>{unit}</span></div>)}
      </div>}
    </div>
    <footer><span>{date}</span><span>Times shown in your timezone</span></footer>
  </section>;
}

export function RaceRoomsIndex() {
  const [rooms, setRooms] = useState<RaceRoom[]>([]);
  const [total, setTotal] = useState(0);
  const [season, setSeason] = useState("2026");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [mode, setMode] = useState("all");
  const [sort, setSort] = useState("race_date_desc");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);
  const [nextSession, setNextSession] = useState<UpcomingSession | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    getSeason(controller.signal).then((calendar) => {
      const sessions = calendar.races.flatMap((race) => (race.sessions ?? [{ name: "Race", starts_at: race.race_start }]).map((session) => ({
        ...session,
        race_name: race.race_name,
        circuit_name: race.circuit_name,
        country: race.country,
        round_number: race.round_number,
      })));
      const cutoff = Date.now() - 3 * 60 * 60 * 1000;
      setNextSession(sessions.filter((session) => new Date(session.starts_at).getTime() > cutoff).sort((a, b) => new Date(a.starts_at).getTime() - new Date(b.starts_at).getTime())[0] ?? null);
    }).catch(() => setNextSession(null));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setRefreshing(true);
      setError(null);
      const params = new URLSearchParams({ season, sort, limit: "100" });
      if (search.trim()) params.set("search", search.trim());
      if (status !== "all") params.set("status", status);
      if (mode !== "all") params.set("mode", mode);
      getRaceRooms(params, controller.signal).then((data) => { setRooms(data.rooms); setTotal(data.total); }).catch((reason: Error) => {
        if (reason.name !== "AbortError") setError("Race Rooms could not reach the control plane. Check the API and try again.");
      }).finally(() => { setLoading(false); setRefreshing(false); });
    }, search ? 250 : 0);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [mode, retryKey, search, season, sort, status]);

  const sections = useMemo(() => {
    return {
      open: rooms.filter((room) => room.mode !== "archived" && room.status !== "completed"),
      archive: rooms.filter((room) => room.mode === "archived" || room.status === "completed"),
    };
  }, [rooms]);
  const filtersActive = search !== "" || status !== "all" || mode !== "all" || sort !== "race_date_desc";
  const resetFilters = () => { setSearch(""); setStatus("all"); setMode("all"); setSort("race_date_desc"); };

  return <main className="rooms-shell track-grid">
    <nav className="rooms-nav" aria-label="Primary"><Link href="/race-rooms" className="rooms-brand"><i className="brand-mark" /> <span>APEX ARENA</span></Link><span>{season} SEASON · RACE ROOMS</span><ThemeToggle /></nav>
    <header className="rooms-hero"><p className="section-kicker">Five minds. One race.</p><h1>Race <em>Rooms</em></h1><p>Enter live and archived rooms where specialist AI agents discuss races using telemetry, timing and race-control data.</p><div className="hero-proof"><span><b>5</b> specialist agents</span><span><b>Event-linked</b> evidence</span><span><b>2026</b> season</span></div></header>
    <section className="room-controls" aria-label="Find Race Rooms" aria-busy={refreshing}>
      <label className="search-control"><span>Search rooms</span><span className="input-shell"><span aria-hidden>⌕</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Race, circuit or country" /></span></label>
      <label><span>Season</span><select value={season} onChange={(event) => setSeason(event.target.value)}><option value="2026">2026</option></select></label>
      <label><span>Status</span><select value={status} onChange={(event) => setStatus(event.target.value)}><option value="all">All statuses</option><option value="live">Live</option><option value="replaying">Replaying</option><option value="paused">Paused</option><option value="ready">Ready</option><option value="completed">Completed</option><option value="unavailable">Unavailable</option></select></label>
      <label><span>Mode</span><select value={mode} onChange={(event) => setMode(event.target.value)}><option value="all">All modes</option><option value="live">Live</option><option value="replay">Replay</option><option value="development">Development</option><option value="archived">Archive</option></select></label>
      <label><span>Sort</span><select value={sort} onChange={(event) => setSort(event.target.value)}><option value="race_date_desc">Newest race</option><option value="race_date_asc">Oldest race</option><option value="latest_activity">Latest activity</option></select></label>
      {filtersActive && <button className="clear-filters" type="button" onClick={resetFilters}>Reset</button>}
    </section>
    <div className="results-summary"><span>{refreshing ? "Refreshing rooms…" : `${total} ${total === 1 ? "room" : "rooms"}`}</span>{filtersActive && <span>Filtered view</span>}</div>
    {loading && <div className="room-skeletons" role="status" aria-label="Loading Race Rooms"><span /><span /><span /></div>}
    {error && <div className="room-state room-state--error" role="alert"><b>Connection interrupted</b><p>{error}</p><button className="control-button" type="button" onClick={() => setRetryKey((value) => value + 1)}>Try again</button></div>}
    {!loading && !error && nextSession && <NextSession session={nextSession} now={now} />}
    {!error && !!sections.open.length && <section><div className="section-heading"><div><p className="section-kicker">Open rooms</p><h2>Current conversations</h2></div><span>{sections.open.length} available</span></div><div className="room-grid">{sections.open.map((room) => <RoomCard key={room.id} room={room} />)}</div></section>}
    {!error && !!sections.archive.length && <section><div className="section-heading"><div><p className="section-kicker">Season archive</p><h2>Completed races</h2></div><span>{sections.archive.length} archived</span></div><div className="room-grid room-grid--compact">{sections.archive.map((room) => <RoomCard key={room.id} room={room} />)}</div></section>}
    {!loading && !error && !rooms.length && <div className="room-state"><span aria-hidden>◌</span><b>No rooms match this view.</b><p>Try a different race, mode, or status.</p>{filtersActive && <button className="control-button" type="button" onClick={resetFilters}>Reset filters</button>}</div>}
  </main>;
}
