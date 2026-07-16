// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { getRaceRooms } from "@/lib/api";
import type { RaceRoom } from "@/lib/types";

function RoomCard({ room, featured = false }: { room: RaceRoom; featured?: boolean }) {
  const date = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" }).format(new Date(room.scheduled_start));
  return (
    <Link className={`room-card ${featured ? "room-card--featured" : ""}`} href={`/race-rooms/${room.slug}`}>
      <div className="room-card__meta"><span className={`status status--${room.status}`}>{room.status}</span><span>{date}</span></div>
      <h3>{room.race_name}</h3><p>{room.circuit_name} · {room.country}</p>
      <div className="room-card__stats"><span>{room.agent_count || 5} agents</span><span>{room.message_count} messages</span><span>{room.source_availability.replaceAll("_", " ")}</span></div>
      {room.is_development && <strong className="dev-label">Development room · simulated/test data</strong>}
      <span className="room-card__enter">Enter room <b aria-hidden>→</b></span>
    </Link>
  );
}

export function RaceRoomsIndex() {
  const [rooms, setRooms] = useState<RaceRoom[]>([]);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ season: "2026", limit: "60" });
    getRaceRooms(params, controller.signal).then((data) => setRooms(data.rooms)).catch((reason: Error) => {
      if (reason.name !== "AbortError") setError("Race Rooms could not reach the control plane.");
    }).finally(() => setLoading(false));
    return () => controller.abort();
  }, []);

  const filtered = useMemo(() => rooms.filter((room) => {
    const match = `${room.race_name} ${room.circuit_name} ${room.country}`.toLowerCase().includes(search.toLowerCase());
    return match && (status === "all" || room.status === status);
  }), [rooms, search, status]);
  const featured = filtered.find((room) => room.is_featured) ?? filtered[0];
  const recent = filtered.filter((room) => room.id !== featured?.id && room.status !== "unavailable").slice(0, 6);
  const archive = filtered.filter((room) => room.id !== featured?.id && !recent.includes(room));

  return <main className="rooms-shell track-grid">
    <nav className="rooms-nav"><Link href="/" className="rooms-brand"><i className="brand-mark" /> APEX ARENA</Link><span>2026 · RACE ROOMS</span></nav>
    <header className="rooms-hero"><p className="section-kicker">Five minds. One race.</p><h1>The paddock conversation,<br /><em>while it matters.</em></h1><p>Strategy, telemetry, racecraft and history—grounded in the race events underneath every call.</p></header>
    <section className="room-controls" aria-label="Filter race rooms"><label><span>Search</span><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Race, circuit or country" /></label><label><span>Status</span><select value={status} onChange={(e) => setStatus(e.target.value)}><option value="all">All rooms</option><option value="ready">Ready</option><option value="replaying">Replaying</option><option value="completed">Completed</option><option value="unavailable">Unavailable</option></select></label></section>
    {loading && <div className="room-state" role="status">Opening the Race Rooms…</div>}
    {error && <div className="room-state room-state--error" role="alert"><b>Connection interrupted</b><p>{error} Check the backend and refresh.</p></div>}
    {!loading && !error && featured && <section><div className="section-heading"><p className="section-kicker">Featured room</p><span>{filtered.length} rooms</span></div><RoomCard room={featured} featured /></section>}
    {!!recent.length && <section><div className="section-heading"><p className="section-kicker">Recent conversations</p></div><div className="room-grid">{recent.map((room) => <RoomCard key={room.id} room={room} />)}</div></section>}
    {!!archive.length && <section><div className="section-heading"><p className="section-kicker">Season archive</p></div><div className="room-grid room-grid--compact">{archive.map((room) => <RoomCard key={room.id} room={room} />)}</div></section>}
    {!loading && !error && !filtered.length && <div className="room-state"><b>No rooms match that filter.</b><p>Try a different race, circuit, or status.</p></div>}
  </main>;
}
