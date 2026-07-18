// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import { AppNavigation } from "@/components/navigation/app-navigation";
import { CircuitOutline } from "@/components/race-rooms/circuit-outline";
import { getRaceRoomEvents } from "@/lib/api";
import type { EventSessionSummary, EventWeekendStatus, RaceRoomEvent } from "@/lib/types";

const SESSION_ORDER = ["SPRINT_QUALIFYING", "SPRINT", "QUALIFYING", "RACE"];

function formatDate(value: string, includeTime = false): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Schedule pending";
  return new Intl.DateTimeFormat(undefined, includeTime
    ? { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit", timeZoneName: "short" }
    : { day: "numeric", month: "short", year: "numeric" }).format(date);
}

function friendlyStatus(status: string): string {
  const labels: Record<string, string> = {
    live: "Live now",
    completed: "Completed",
    replay_ready: "Replay ready",
    ready: "Ready",
    scheduled: "Upcoming",
    upcoming: "Upcoming",
    provider_pending: "Data pending",
    ingesting: "Preparing",
    partial: "Partial data",
    results_only: "Results only",
    unavailable: "Unavailable",
    failed: "Unavailable",
  };
  return labels[status.toLowerCase()] ?? status.replaceAll("_", " ");
}

function availabilityLabel(session: EventSessionSummary): string {
  if (session.replay_available) return session.data_availability === "limited_telemetry" ? "Replay · limited telemetry" : "Telemetry replay";
  if (session.results_available) return "Results available";
  if (session.data_availability === "limited_telemetry") return "Some timing data missing";
  if (session.data_availability === "timing_only") return "Timing data only";
  if (session.data_availability === "telemetry") return "Telemetry available";
  if (session.data_availability === "unavailable" && session.status === "scheduled") return "Live feed arms at session start";
  if (session.data_availability === "unavailable" && session.status === "completed") return "Provider data not published yet";
  if (session.data_availability === "unavailable") return "Waiting for the live provider feed";
  if (session.status === "ingesting" || session.status === "provider_pending") return "Session data is being prepared";
  return "Schedule confirmed";
}

function sessionOrder(session: EventSessionSummary): number {
  const known = SESSION_ORDER.indexOf(session.session_type.toUpperCase());
  return known === -1 ? 99 : known;
}

function sortedSessions(sessions: EventSessionSummary[]): EventSessionSummary[] {
  return [...sessions].sort((a, b) => {
    const aTime = new Date(a.actual_start ?? a.scheduled_start).getTime();
    const bTime = new Date(b.actual_start ?? b.scheduled_start).getTime();
    if (Number.isFinite(aTime) && Number.isFinite(bTime) && aTime !== bTime) return aTime - bTime;
    return sessionOrder(a) - sessionOrder(b);
  });
}

function countdownParts(milliseconds: number) {
  const secondsTotal = Math.max(0, Math.floor(milliseconds / 1000));
  return {
    days: Math.floor(secondsTotal / 86400),
    hours: Math.floor((secondsTotal % 86400) / 3600),
    minutes: Math.floor((secondsTotal % 3600) / 60),
    seconds: secondsTotal % 60,
  };
}

function isPublicEvent(event: RaceRoomEvent): boolean {
  return !event.is_development && !event.event_slug.toLowerCase().includes("validation");
}

type OpenPreview = (event: RaceRoomEvent, session?: EventSessionSummary) => void;

function SessionAction({ event, session, onPreview }: { event: RaceRoomEvent; session: EventSessionSummary; onPreview: OpenPreview }) {
  const readOnly = event.weekend_status === "upcoming" || session.eligibility === "future_read_only" || session.status === "scheduled";
  const canOpenRoom = Boolean(session.room_slug) && !readOnly;
  const content = <>
    <span className="event-session__identity"><b>{session.display_name}</b><small>{formatDate(session.scheduled_start, true)}</small></span>
    <span className="event-session__state"><span className={`session-status session-status--${session.status}`}>{friendlyStatus(session.status)}</span><small>{availabilityLabel(session)}</small></span>
    <span className="event-session__arrow" aria-hidden>{canOpenRoom ? "→" : "⌁"}</span>
  </>;

  if (canOpenRoom && session.room_slug) {
    return <Link className="event-session" href={`/race-rooms/${session.room_slug}`} aria-label={`Open ${event.event_name} ${session.display_name}`}>{content}</Link>;
  }
  return <button className="event-session event-session--preview" type="button" onClick={() => onPreview(event, session)} aria-label={`View schedule for ${event.event_name} ${session.display_name}`}>{content}</button>;
}

function EventCard({ event, onPreview }: { event: RaceRoomEvent; onPreview: OpenPreview }) {
  const sessions = sortedSessions(event.sessions);
  return <article className={`event-card event-card--${event.weekend_status}`} aria-labelledby={`event-${event.event_id}`}>
    <header className="event-card__header">
      <div className="event-card__title">
        <div className="event-card__eyebrow"><span>Round {event.round}</span>{event.is_sprint_weekend && <span className="sprint-badge">Sprint weekend</span>}</div>
        <h3 id={`event-${event.event_id}`}>{event.event_name}</h3>
        <p>{event.circuit_name} · {event.country}</p>
      </div>
      <div className="event-card__side"><CircuitOutline circuitName={event.circuit_name} eventName={event.event_name} compact /><div className="event-card__date"><b>{formatDate(event.weekend_start)}</b><span>{event.weekend_status === "completed" ? "Weekend complete" : event.weekend_status === "live" ? "This weekend" : "Scheduled"}</span></div></div>
    </header>
    <div className="event-card__sessions" aria-label={`${event.event_name} sessions`}>
      {sessions.map((session) => <SessionAction event={event} session={session} onPreview={onPreview} key={`${event.event_id}-${session.session_type}`} />)}
      {!sessions.length && <p className="event-card__empty">The official session schedule is being confirmed.</p>}
    </div>
    {event.weekend_status === "upcoming" && <button className="event-card__preview" type="button" onClick={() => onPreview(event)}>View weekend schedule <span aria-hidden>→</span></button>}
  </article>;
}

function WeekendCountdown({ events, now, onPreview }: { events: RaceRoomEvent[]; now: number; onPreview: OpenPreview }) {
  const candidate = events.flatMap((event) => event.sessions.map((session) => ({ event, session })))
    .filter(({ session }) => SESSION_ORDER.includes(session.session_type.toUpperCase()))
    .filter(({ session }) => session.status === "live" || new Date(session.scheduled_start).getTime() > now)
    .sort((a, b) => new Date(a.session.scheduled_start).getTime() - new Date(b.session.scheduled_start).getTime())[0];
  if (!candidate) return null;
  const { event, session } = candidate;
  const live = session.status === "live";
  const remaining = countdownParts(new Date(session.scheduled_start).getTime() - now);
  const canOpen = live && Boolean(session.room_slug);
  return <section className="weekend-countdown" aria-labelledby="next-live-session-title">
    <div className="weekend-countdown__copy"><p><span className={live ? "live-pulse" : "signal-pulse"} />{live ? "Live now" : "Next live session"}</p><h2 id="next-live-session-title">{session.display_name}</h2><span>{event.event_name} · {event.circuit_name}</span><time dateTime={session.scheduled_start}>{formatDate(session.scheduled_start, true)}</time></div>
    {live ? <div className="weekend-countdown__live"><strong>On air</strong><span>The session is underway</span></div> : <div className="weekend-countdown__clock" aria-label={`Countdown to ${event.event_name} ${session.display_name}`}>{Object.entries(remaining).map(([unit, value]) => <span key={unit}><b>{String(value).padStart(2, "0")}</b><small>{unit}</small></span>)}</div>}
    {canOpen && session.room_slug ? <Link className="weekend-countdown__action" href={`/race-rooms/${session.room_slug}`}>Join the room <span aria-hidden>→</span></Link> : <button className="weekend-countdown__action" type="button" onClick={() => onPreview(event, session)}>View session details <span aria-hidden>→</span></button>}
  </section>;
}

function CategorySection({ id, title, kicker, events, emptyCopy, onPreview }: { id: string; title: string; kicker: string; events: RaceRoomEvent[]; emptyCopy: string; onPreview: OpenPreview }) {
  return <section className="event-category" aria-labelledby={id}>
    <div className="section-heading event-category__heading"><div><p className="section-kicker">{kicker}</p><h2 id={id}>{title}</h2></div><span>{events.length} {events.length === 1 ? "weekend" : "weekends"}</span></div>
    {events.length
      ? <div className="event-grid">{events.map((event) => <EventCard event={event} onPreview={onPreview} key={event.event_id} />)}</div>
      : <div className="event-category__empty"><span aria-hidden>○</span><p>{emptyCopy}</p></div>}
  </section>;
}

function UpcomingPreview({ event, focusSession, onClose }: { event: RaceRoomEvent; focusSession?: string; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const onKeyDown = (keyboardEvent: KeyboardEvent) => {
      if (keyboardEvent.key === "Escape") { keyboardEvent.preventDefault(); onClose(); return; }
      if (keyboardEvent.key !== "Tab" || !panelRef.current) return;
      const focusable = [...panelRef.current.querySelectorAll<HTMLElement>("button:not([disabled]), a[href]")];
      const first = focusable[0];
      const last = focusable.at(-1) ?? first;
      if (!first) return;
      if (keyboardEvent.shiftKey && document.activeElement === first) { keyboardEvent.preventDefault(); last.focus(); }
      else if (!keyboardEvent.shiftKey && document.activeElement === last) { keyboardEvent.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKeyDown);
    document.body.classList.add("drawer-open");
    window.requestAnimationFrame(() => closeRef.current?.focus());
    return () => { document.removeEventListener("keydown", onKeyDown); document.body.classList.remove("drawer-open"); previouslyFocused?.focus(); };
  }, [onClose]);

  return <div className="upcoming-preview-layer">
    <button className="upcoming-preview__backdrop" type="button" aria-label="Close upcoming event details" onClick={onClose} />
    <aside ref={panelRef} className="upcoming-preview" role="dialog" aria-modal="true" aria-labelledby="upcoming-preview-title">
      <header><div><p className="section-kicker">Upcoming event</p><h2 id="upcoming-preview-title">{event.event_name}</h2><p>{event.circuit_name} · {event.country}</p></div><button ref={closeRef} className="icon-button" type="button" aria-label="Close upcoming event details" onClick={onClose}>×</button></header>
      <div className="upcoming-preview__facts"><span>Round {event.round}</span><span>{formatDate(event.weekend_start)}</span>{event.is_sprint_weekend && <span className="sprint-badge">Sprint weekend</span>}</div>
      <section aria-labelledby="weekend-schedule-title"><h3 id="weekend-schedule-title">Weekend schedule</h3><ol>{sortedSessions(event.sessions).map((session) => <li className={focusSession === session.session_type ? "upcoming-preview__focused" : ""} key={session.session_type}><span><b>{session.display_name}</b><small>{friendlyStatus(session.status)}</small></span><time dateTime={session.scheduled_start}>{formatDate(session.scheduled_start, true)}</time></li>)}</ol></section>
      <div className="upcoming-preview__notice"><span aria-hidden>◷</span><div><b>Room opens when session data becomes available.</b><p>This preview uses the official schedule. Opening it does not create a room, start a replay, or generate a conversation.</p></div></div>
      <button className="control-button" type="button" onClick={onClose}>Back to events</button>
    </aside>
  </div>;
}

export function RaceRoomsIndex() {
  const [events, setEvents] = useState<RaceRoomEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [season, setSeason] = useState("2026");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [sessionType, setSessionType] = useState("all");
  const [weekendFormat, setWeekendFormat] = useState("all");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);
  const [previewSlug, setPreviewSlug] = useState<string | null>(() => typeof window === "undefined" ? null : new URL(window.location.href).searchParams.get("event"));
  const [previewSession, setPreviewSession] = useState<string | undefined>(() => typeof window === "undefined" ? undefined : new URL(window.location.href).searchParams.get("session") ?? undefined);
  const [now, setNow] = useState(() => Date.now());
  const previewWasPushed = useRef(false);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const onPopState = () => {
      const url = new URL(window.location.href);
      setPreviewSlug(url.searchParams.get("event"));
      setPreviewSession(url.searchParams.get("session") ?? undefined);
      previewWasPushed.current = false;
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setRefreshing(true);
      setError(null);
      const params = new URLSearchParams({ season, limit: "100", offset: "0" });
      if (search.trim()) params.set("search", search.trim());
      if (status !== "all") params.set("status", status);
      if (sessionType !== "all") params.set("session_type", sessionType);
      if (weekendFormat !== "all") params.set("is_sprint_weekend", String(weekendFormat === "sprint"));
      getRaceRoomEvents(params, controller.signal).then((data) => {
        setEvents(data.events.filter(isPublicEvent));
        setTotal(data.total);
      }).catch((reason: Error) => {
        if (reason.name !== "AbortError") setError("Race Rooms could not load the season schedule. Check the API and try again.");
      }).finally(() => { setLoading(false); setRefreshing(false); });
    }, search ? 250 : 0);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [retryKey, search, season, sessionType, status, weekendFormat]);

  const sections = useMemo(() => {
    const ordered = [...events].sort((a, b) => new Date(a.weekend_start).getTime() - new Date(b.weekend_start).getTime());
    const group = (value: EventWeekendStatus) => ordered.filter((event) => event.weekend_status === value);
    return { live: group("live"), completed: group("completed"), upcoming: group("upcoming") };
  }, [events]);
  const previewEvent = events.find((event) => event.event_slug === previewSlug);
  const filtersActive = search !== "" || status !== "all" || sessionType !== "all" || weekendFormat !== "all";
  const resetFilters = () => { setSearch(""); setStatus("all"); setSessionType("all"); setWeekendFormat("all"); };
  const openPreview: OpenPreview = (event, session) => {
    const url = new URL(window.location.href);
    url.searchParams.set("event", event.event_slug);
    if (session) url.searchParams.set("session", session.session_type); else url.searchParams.delete("session");
    window.history.pushState({ apexUpcomingPreview: true }, "", `${url.pathname}${url.search}`);
    previewWasPushed.current = true;
    setPreviewSlug(event.event_slug);
    setPreviewSession(session?.session_type);
  };
  const closePreview = () => {
    if (previewWasPushed.current) { window.history.back(); return; }
    const url = new URL(window.location.href);
    url.searchParams.delete("event");
    url.searchParams.delete("session");
    window.history.replaceState(null, "", `${url.pathname}${url.search}`);
    setPreviewSlug(null);
    setPreviewSession(undefined);
  };

  return <main className="rooms-shell track-grid">
    <AppNavigation />
    <header className="rooms-hero"><p className="section-kicker">The 2026 season, session by session</p><h1>Race <em>Rooms</em></h1><p>Follow each Grand Prix weekend as one clear story. Open completed replays, join live sessions, or explore what is coming next.</p></header>
    <section className="room-controls" aria-label="Find Race Rooms" aria-busy={refreshing}>
      <div className="room-controls__mobile-heading"><span>Search &amp; filters</span><button type="button" aria-expanded={filtersOpen} aria-controls="event-filter-fields" onClick={() => setFiltersOpen((value) => !value)}>{filtersActive ? "Active" : "All events"} <b aria-hidden>{filtersOpen ? "−" : "+"}</b></button></div>
      <div id="event-filter-fields" className={`room-controls__fields ${filtersOpen ? "room-controls__fields--open" : ""}`}>
        <label className="search-control"><span>Search events</span><span className="input-shell"><span aria-hidden>⌕</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Grand Prix, circuit or country" /></span></label>
        <label><span>Season</span><select value={season} onChange={(event) => setSeason(event.target.value)}><option value="2026">2026</option></select></label>
        <label><span>Category</span><select value={status} onChange={(event) => setStatus(event.target.value)}><option value="all">All categories</option><option value="live">Live this weekend</option><option value="completed">Completed</option><option value="upcoming">Upcoming</option></select></label>
        <label><span>Session</span><select value={sessionType} onChange={(event) => setSessionType(event.target.value)}><option value="all">All sessions</option><option value="QUALIFYING">Qualifying</option><option value="SPRINT_QUALIFYING">Sprint Qualifying</option><option value="SPRINT">Sprint</option><option value="RACE">Race</option></select></label>
        <label><span>Weekend</span><select value={weekendFormat} onChange={(event) => setWeekendFormat(event.target.value)}><option value="all">All formats</option><option value="sprint">Sprint weekends</option><option value="standard">Standard weekends</option></select></label>
        {filtersActive && <button className="clear-filters" type="button" onClick={resetFilters}>Reset</button>}
      </div>
    </section>
    <div className="results-summary" aria-live="polite"><span>{refreshing ? "Refreshing weekends…" : `${total} ${total === 1 ? "weekend" : "weekends"}`}</span>{filtersActive && <span>Grouped results</span>}</div>
    {loading && <div className="event-skeletons" role="status" aria-label="Loading race weekends"><span /><span /><span /></div>}
    {error && <div className="room-state room-state--error" role="alert"><b>Schedule unavailable</b><p>{error}</p><button className="control-button" type="button" onClick={() => setRetryKey((value) => value + 1)}>Try again</button></div>}
    {!loading && !error && <div className="event-categories">
      <WeekendCountdown events={events} now={now} onPreview={openPreview} />
      <CategorySection id="live-weekend-title" kicker="The weekend unfolding now" title="Live This Weekend" events={sections.live} emptyCopy="There is no active race weekend right now." onPreview={openPreview} />
      <CategorySection id="completed-events-title" kicker="Watch the season so far" title="Completed Events" events={sections.completed} emptyCopy="No completed events match these filters." onPreview={openPreview} />
      <CategorySection id="upcoming-events-title" kicker="What comes next" title="Upcoming Events" events={sections.upcoming} emptyCopy="No upcoming events match these filters." onPreview={openPreview} />
    </div>}
    {!loading && !error && !events.length && filtersActive && <div className="room-state"><span aria-hidden>◌</span><b>No weekends match this view.</b><p>Try another event, session, or weekend format.</p><button className="control-button" type="button" onClick={resetFilters}>Reset filters</button></div>}
    {previewEvent && <UpcomingPreview event={previewEvent} focusSession={previewSession} onClose={closePreview} />}
  </main>;
}
