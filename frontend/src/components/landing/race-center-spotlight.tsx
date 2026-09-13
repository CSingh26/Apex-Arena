// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { getDriverStandings, getRaceRoomEvents } from "@/lib/api";
import { appRoutes } from "@/lib/app-paths";
import type { DriverStanding, EventSessionSummary, RaceRoomEvent } from "@/lib/types";
import styles from "./race-center-spotlight.module.css";

type RaceCenterData = { events: RaceRoomEvent[]; leader: DriverStanding | null };

function sessionTime(session: EventSessionSummary): number {
  return new Date(session.actual_start ?? session.scheduled_start).getTime();
}

function formatSessionTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Schedule pending";
  return new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }).format(date);
}

function timeUntil(value: string, now: number): string {
  const difference = new Date(value).getTime() - now;
  if (!Number.isFinite(difference) || difference <= 0) return "Starting soon";
  const hours = Math.floor(difference / 3_600_000);
  const days = Math.floor(hours / 24);
  if (days) return `${days}d ${hours % 24}h`;
  const minutes = Math.max(1, Math.floor((difference % 3_600_000) / 60_000));
  return `${hours}h ${minutes}m`;
}

export function RaceCenterSpotlight() {
  const [data, setData] = useState<RaceCenterData | null>(null);
  const [error, setError] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ season: "2026", limit: "100", offset: "0" });
    const load = async () => {
      try {
        const [weekends, standings] = await Promise.all([getRaceRoomEvents(params, controller.signal), getDriverStandings(controller.signal)]);
        setData({ events: weekends.events, leader: standings.standings[0] ?? null });
      } catch (reason) {
        if (!(reason instanceof Error) || reason.name !== "AbortError") setError(true);
      }
    };
    void load();
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, []);

  const view = useMemo(() => {
    if (!data) return null;
    const ordered = [...data.events].sort((a, b) => new Date(a.weekend_start).getTime() - new Date(b.weekend_start).getTime());
    const active = ordered.find((event) => event.weekend_status === "live");
    const next = active ?? ordered.find((event) => event.weekend_status === "upcoming") ?? ordered.at(-1);
    const latest = [...ordered].reverse().find((event) => event.weekend_status === "completed");
    const nextSession = next?.sessions
      .filter((session) => session.status === "live" || sessionTime(session) > now)
      .sort((a, b) => sessionTime(a) - sessionTime(b))[0] ?? next?.sessions.at(-1);
    return { event: next, latest, session: nextSession, live: Boolean(active) };
  }, [data, now]);

  if (!data && !error) return <section className={styles.shell} aria-label="Race Center loading" aria-busy="true"><div className={styles.skeleton}><span /><span /><span /></div></section>;

  if (error || !data || !view?.event) return <section className={styles.shell} aria-labelledby="race-center-title"><div className={styles.unavailable}><span aria-hidden>◌</span><div><p>Race Center</p><h2 id="race-center-title">The live signal is taking a formation lap.</h2><p>The season schedule is temporarily unavailable. Race Rooms and standings are still ready to explore.</p></div><Link href={appRoutes.rooms}>Browse Race Rooms <span aria-hidden>→</span></Link></div></section>;

  const { event, latest, session, live } = view;
  const openSession = session?.room_slug && session.status !== "scheduled";
  return <section className={styles.shell} aria-labelledby="race-center-title">
    <div className={styles.rail}><span className={live ? styles.live : ""}>{live ? "Live weekend" : "Race Center"}</span><span>Round {event.round} · 2026</span></div>
    <div className={styles.layout}>
      <div className={styles.primary}>
        <p>{event.country} · {event.circuit_name}</p>
        <h2 id="race-center-title">{event.event_name}</h2>
        {session && <div className={styles.session}>
          <div><span>{live ? "Happening now" : "Next session"}</span><strong>{session.display_name}</strong><small>{formatSessionTime(session.scheduled_start)}</small></div>
          {!live && <b aria-label={`${timeUntil(session.scheduled_start, now)} until the session`}>{timeUntil(session.scheduled_start, now)}</b>}
        </div>}
        <div className={styles.actions}>
          <Link className={styles.primaryAction} href={openSession && session?.room_slug ? appRoutes.room(session.room_slug) : `${appRoutes.rooms}?event=${event.event_slug}`}>{openSession ? "Enter the Race Room" : "View weekend"}<span aria-hidden>↗</span></Link>
          <Link href={appRoutes.standings}>Championship standings <span aria-hidden>→</span></Link>
        </div>
      </div>
      <div className={styles.context}>
        <div><span>Championship leader</span><strong>{data.leader?.full_name ?? "Standings updating"}</strong><small>{data.leader ? `${data.leader.points} points · ${data.leader.team_name ?? "Independent"}` : "Current-season context will appear here."}</small></div>
        <div><span>Most recent</span><strong>{latest?.event_name ?? "Season opening"}</strong><small>{latest ? "Discussion and session replays available" : "The first result will appear after race day."}</small></div>
        <div><span>Weekend format</span><strong>{event.is_sprint_weekend ? "Sprint weekend" : "Grand Prix weekend"}</strong><small>{event.sessions.length} scheduled sessions in Apex Arena</small></div>
      </div>
    </div>
  </section>;
}
