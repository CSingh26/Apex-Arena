// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { getEngineStatus, getMessageEvidence, getRaceRoom, getRoomMessages, roomStreamUrl, startRoomReplay, updateRoomPlayback } from "@/lib/api";
import type { AgentProfile, EngineStatus, MessageEvidence, MessageTopic, RaceRoomDetailResponse, RoomMessage, RoomPlayback } from "@/lib/types";
import { mergeRoomMessages } from "@/lib/room-state";
import { ThemeToggle } from "@/components/race-rooms/theme-toggle";

const TOPICS: MessageTopic[] = ["strategy", "pace", "racecraft", "incident", "pit_stop", "tyres", "championship", "summary"];
const initials = (name: string) => name.split(" ").map((part) => part[0]).join("");

function MessageCard({ message, agent, slug }: { message: RoomMessage; agent?: AgentProfile; slug: string }) {
  const [evidence, setEvidence] = useState<MessageEvidence[] | null>(null);
  const [open, setOpen] = useState(false);
  const inspect = async () => { setOpen((value) => !value); if (!evidence) setEvidence((await getMessageEvidence(slug, message.id)).evidence); };
  return <article className={`message message--${agent?.avatar_key ?? "host"}`}>
    <div className="agent-avatar" aria-hidden>{initials(agent?.name ?? "Unknown")}</div>
    <div className="message__body"><div className="message__meta"><strong>{agent?.name ?? "Race Room"}</strong><span>{agent?.role}</span><span>{message.lap_number == null ? "Session" : `Lap ${message.lap_number}`}</span><span>{message.topic.replaceAll("_", " ")}</span></div>
      <p>{message.content}</p>
      <button className="evidence-toggle" onClick={inspect} aria-expanded={open}>Evidence · {message.evidence_status} <span aria-hidden>{open ? "−" : "+"}</span></button>
      {open && <div className="evidence"><span>{message.confidence} confidence · {message.generated_by}</span>{evidence === null ? <p>Loading source references…</p> : evidence.length ? evidence.map((item) => <p key={item.id}><b>{item.source_provider}</b> · {item.metric_name ?? item.evidence_type}: {String(item.metric_value ?? item.source_reference)} {item.unit}</p>) : <p>No detailed telemetry evidence is available for this message.</p>}</div>}
    </div>
  </article>;
}

function Diagnostics({ status, detail }: { status: EngineStatus | null; detail: RaceRoomDetailResponse }) {
  return <details className="diagnostics"><summary>System diagnostics</summary><div><p><b>Room source</b> {detail.room.source_availability}</p><p><b>Control plane</b> {status?.status ?? "checking"}</p><p><b>Database</b> {status?.database.status ?? "checking"}</p><p><b>Redis stream</b> {status?.redis.status ?? "checking"}</p><p><b>Session</b> {detail.room.session_key ?? "not linked"}</p><p>{detail.data_notice}</p></div></details>;
}

export function RoomExperience({ slug }: { slug: string }) {
  const [detail, setDetail] = useState<RaceRoomDetailResponse | null>(null);
  const [messages, setMessages] = useState<RoomMessage[]>([]);
  const [playback, setPlayback] = useState<RoomPlayback | null>(null);
  const [engine, setEngine] = useState<EngineStatus | null>(null);
  const [agentFilter, setAgentFilter] = useState("all"); const [topicFilter, setTopicFilter] = useState("all");
  const [lap, setLap] = useState(""); const [connected, setConnected] = useState(false); const [error, setError] = useState<string | null>(null);

  const mergeMessages = useCallback((incoming: RoomMessage[]) => setMessages((current) => {
    return mergeRoomMessages(current, incoming);
  }), []);
  useEffect(() => { const controller = new AbortController(); Promise.all([getRaceRoom(slug, controller.signal), getRoomMessages(slug, "limit=250", controller.signal), getEngineStatus(controller.signal)]).then(([room, feed, status]) => { setDetail(room); setPlayback(room.playback); mergeMessages(feed.messages); setEngine(status); }).catch((reason: Error) => { if (reason.name !== "AbortError") setError("This room is not available right now."); }); return () => controller.abort(); }, [slug, mergeMessages]);
  useEffect(() => { if (!detail) return; const source = new EventSource(roomStreamUrl(slug)); source.addEventListener("open", () => setConnected(true)); source.addEventListener("error", () => setConnected(false)); source.addEventListener("room_message", (event) => mergeMessages([JSON.parse((event as MessageEvent).data) as RoomMessage])); source.addEventListener("playback_state", (event) => setPlayback(JSON.parse((event as MessageEvent).data) as RoomPlayback)); return () => source.close(); }, [detail, slug, mergeMessages]);
  const visible = useMemo(() => messages.filter((message) => (agentFilter === "all" || message.agent_id === agentFilter) && (topicFilter === "all" || message.topic === topicFilter) && (!lap || message.lap_number === Number(lap))), [messages, agentFilter, topicFilter, lap]);
  const control = async (body: object) => setPlayback((await updateRoomPlayback(slug, body)).playback);
  if (error) return <main className="room-page"><div className="room-state room-state--error"><b>Room unavailable</b><p>{error}</p><Link href="/race-rooms">Return to Race Rooms</Link></div></main>;
  if (!detail || !playback) return <main className="room-page"><div className="room-state" role="status">Joining the room…</div></main>;
  const { room, agents } = detail;
  return <main className="room-page track-grid">
    <nav className="room-topbar"><Link href="/race-rooms">← All rooms</Link><span className={`connection ${connected ? "connection--live" : ""}`}>{connected ? "Live stream" : "Reconnecting"}</span><ThemeToggle /></nav>
    <header className="room-header"><div><p className="section-kicker">{room.season} · Round {room.round_number ?? "—"} · {room.mode}</p><h1>{room.race_name}</h1><p>{room.circuit_name} · {room.country}</p>{room.is_development && <strong className="dev-label">Development room · simulated/test data</strong>}</div><div className="lap-display"><span>Lap</span><b>{room.current_lap ?? "—"}</b><small>/ {room.total_laps ?? "—"}</small></div></header>
    <div className="room-layout"><aside className="agent-panel"><p className="section-kicker">In this room</p>{agents.map((agent) => <button key={agent.id} onClick={() => setAgentFilter(agentFilter === agent.id ? "all" : agent.id)} className={agentFilter === agent.id ? "selected" : ""}><span className="agent-avatar">{initials(agent.name)}</span><span><b>{agent.name}</b><small>{agent.role}</small></span></button>)}<Diagnostics status={engine} detail={detail} /></aside>
      <section className="conversation"><div className="playback"><button onClick={() => playback.is_paused ? control({ action: "resume" }) : control({ action: "pause" })}>{playback.is_paused ? "▶ Play" : "Ⅱ Pause"}</button><select aria-label="Playback speed" value={playback.playback_speed} onChange={(event) => control({ action: "speed", playback_speed: Number(event.target.value) })}><option value="0.5">0.5×</option><option value="1">1×</option><option value="2">2×</option><option value="4">4×</option></select><button onClick={async () => setPlayback((await startRoomReplay(slug)).playback)}>Restart replay</button><span>Sequence {playback.current_sequence}</span></div>
        <div className="feed-filters"><select value={topicFilter} onChange={(event) => setTopicFilter(event.target.value)} aria-label="Filter by topic"><option value="all">All topics</option>{TOPICS.map((topic) => <option key={topic} value={topic}>{topic.replaceAll("_", " ")}</option>)}</select><label>Lap <input type="number" min="1" max={room.total_laps ?? undefined} value={lap} onChange={(event) => setLap(event.target.value)} /></label>{lap && <button onClick={() => control({ action: "seek", lap_number: Number(lap) })}>Jump to lap</button>}<span>{visible.length} messages</span></div>
        <div className="message-feed">{visible.map((message) => <MessageCard key={message.id} message={message} agent={agents.find((agent) => agent.id === message.agent_id)} slug={slug} />)}{!visible.length && <div className="room-state"><b>The room is quiet here.</b><p>Change the filters or wait for a significant race moment.</p></div>}</div>
      </section></div>
  </main>;
}
