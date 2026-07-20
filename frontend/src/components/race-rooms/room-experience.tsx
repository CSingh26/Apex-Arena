// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { AppNavigation } from "@/components/navigation/app-navigation";
import { ApexRaceLoader } from "@/components/loading/apex-race-loader";
import { AgentRoster } from "@/components/race-rooms/agent-roster";
import { CircuitOutline } from "@/components/race-rooms/circuit-outline";
import { EvidenceDrawer } from "@/components/race-rooms/evidence-drawer";
import { MessageTimeline } from "@/components/race-rooms/message-timeline";
import { PlaybackControls } from "@/components/race-rooms/playback-controls";
import { RoomContext } from "@/components/race-rooms/room-context";
import { getRaceRoom, getRoomMessages, roomStreamUrl, startRoomReplay, updateRoomPlayback } from "@/lib/api";
import { appRoutes } from "@/lib/app-paths";
import { mergeRoomMessages } from "@/lib/room-state";
import type { PlaybackAction, RaceRoomDetailResponse, ReplayAction, RoomMessage, RoomPlayback, RoomStatus } from "@/lib/types";

type ConnectionState = "connecting" | "live" | "reconnecting" | "degraded";
const ROOM_STATUSES = new Set<RoomStatus>(["pending", "ingesting", "ready", "live", "replaying", "paused", "completed", "failed", "unavailable"]);

export function RoomExperience({ slug }: { slug: string }) {
  const [detail, setDetail] = useState<RaceRoomDetailResponse | null>(null);
  const [messages, setMessages] = useState<RoomMessage[]>([]);
  const [playback, setPlayback] = useState<RoomPlayback | null>(null);
  const [selectedAgent, setSelectedAgent] = useState("all");
  const [selectedMessage, setSelectedMessage] = useState<RoomMessage | null>(null);
  const [nextCursor, setNextCursor] = useState<number | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [controlError, setControlError] = useState<string | null>(null);
  const [controlBusy, setControlBusy] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [streamGeneration, setStreamGeneration] = useState(0);
  const lastSequenceRef = useRef(0);

  const mergeMessages = useCallback((incoming: RoomMessage[]) => {
    if (incoming.length) lastSequenceRef.current = Math.max(lastSequenceRef.current, ...incoming.map((message) => message.sequence));
    setMessages((current) => mergeRoomMessages(current, incoming));
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    Promise.all([
      getRaceRoom(slug, controller.signal),
      getRoomMessages(slug, "after_sequence=0&limit=100", controller.signal),
    ]).then(([room, feed]) => {
      if (!active) return;
      setDetail(room);
      setPlayback(room.playback);
      setMessages([]);
      lastSequenceRef.current = 0;
      mergeMessages(feed.messages);
      setNextCursor(feed.next_cursor);
    }).catch((reason: Error) => {
      if (active && reason.name !== "AbortError") setError(reason.message || "This room is not available right now.");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; controller.abort(); };
  }, [mergeMessages, reloadKey, slug]);

  const roomId = detail?.room.id;
  useEffect(() => {
    if (!roomId) return;
    let disposed = false;
    let source: EventSource | null = null;
    let retryTimer: number | null = null;
    let retryAttempt = 0;

    const connect = () => {
      if (disposed) return;
      setConnection(retryAttempt ? "reconnecting" : "connecting");
      source = new EventSource(roomStreamUrl(slug, lastSequenceRef.current));
      source.addEventListener("open", () => { retryAttempt = 0; setConnection("live"); });
      source.addEventListener("room_message", (event) => {
        const message = JSON.parse((event as MessageEvent).data) as RoomMessage;
        mergeMessages([message]);
      });
      source.addEventListener("playback_state", (event) => {
        setPlayback(JSON.parse((event as MessageEvent).data) as RoomPlayback);
      });
      source.addEventListener("room_status", (event) => {
        const payload = JSON.parse((event as MessageEvent).data) as Record<string, unknown>;
        const nextStatus = String(payload.status ?? "");
        setDetail((current) => current && ROOM_STATUSES.has(nextStatus as RoomStatus) ? { ...current, room: { ...current.room, status: nextStatus as RoomStatus, current_lap: typeof payload.current_lap === "number" ? payload.current_lap : current.room.current_lap } } : current);
      });
      source.addEventListener("connection_status", (event) => {
        const payload = JSON.parse((event as MessageEvent).data) as { status?: string };
        if (payload.status === "degraded") setConnection("degraded");
      });
      source.addEventListener("error", () => {
        source?.close();
        if (disposed) return;
        retryAttempt += 1;
        setConnection(retryAttempt > 3 ? "degraded" : "reconnecting");
        retryTimer = window.setTimeout(connect, Math.min(8000, 750 * 2 ** Math.min(retryAttempt, 4)));
      });
    };
    connect();
    return () => { disposed = true; source?.close(); if (retryTimer != null) window.clearTimeout(retryTimer); };
  }, [mergeMessages, roomId, slug, streamGeneration]);

  const runControl = useCallback(async (action: PlaybackAction) => {
    setControlBusy(true); setControlError(null);
    try {
      const response = await updateRoomPlayback(slug, action);
      setPlayback(response.playback);
      setDetail((current) => current ? { ...current, room: response.room } : current);
    } catch (reason) {
      setControlError(reason instanceof Error ? reason.message : "The replay control did not respond.");
    } finally { setControlBusy(false); }
  }, [slug]);

  const runReplay = useCallback(async (action: ReplayAction) => {
    setControlBusy(true); setControlError(null);
    try {
      const response = await startRoomReplay(slug, action);
      if (action === "restart") {
        setMessages([]);
        setNextCursor(null);
        lastSequenceRef.current = 0;
        setStreamGeneration((value) => value + 1);
      }
      setPlayback(response.playback);
      setDetail((current) => current ? { ...current, room: response.room } : current);
    } catch (reason) {
      setControlError(reason instanceof Error ? reason.message : "The replay could not be started.");
    } finally { setControlBusy(false); }
  }, [slug]);

  const loadMore = useCallback(async () => {
    if (nextCursor == null || loadingMore) return;
    setLoadingMore(true);
    try {
      const feed = await getRoomMessages(slug, `after_sequence=${nextCursor}&limit=100`);
      mergeMessages(feed.messages);
      setNextCursor(feed.next_cursor);
    } catch (reason) {
      setControlError(reason instanceof Error ? reason.message : "More messages could not be loaded.");
    } finally { setLoadingMore(false); }
  }, [loadingMore, mergeMessages, nextCursor, slug]);

  const closeEvidence = useCallback(() => setSelectedMessage(null), []);

  if (loading) return <main className="room-page track-grid"><ApexRaceLoader label="Joining the race room" /></main>;
  if (error || !detail || !playback) return <main className="room-page track-grid"><div className="room-state room-state--error room-state--centered" role="alert"><span aria-hidden>!</span><b>Room unavailable</b><p>{error ?? "The room response was incomplete."}</p><div><button className="control-button" type="button" onClick={() => { setLoading(true); setError(null); setReloadKey((value) => value + 1); }}>Try again</button><Link className="control-button" href={appRoutes.rooms}>All Race Rooms</Link></div></div></main>;

  const { room, agents } = detail;
  const evidenceAgent = selectedMessage ? agents.find((agent) => agent.id === selectedMessage.agent_id) : undefined;
  const qualifying = room.session_type.toUpperCase().includes("QUALIFY") || room.session_type.toUpperCase().includes("SHOOTOUT");
  const progressLabel = qualifying ? "Current phase" : room.status === "live" ? "Current lap" : "Replay lap";
  const progressValue = qualifying ? (room.current_phase ?? "Session") : (playback.current_lap ?? room.current_lap ?? "—");
  return <main className="room-page track-grid">
    <AppNavigation contextLabel={`${room.race_name} · ${room.session_type}`} connection={connection} />
    <Link className="room-breadcrumb" href={appRoutes.rooms}><span aria-hidden>←</span> All Race Rooms</Link>
    <header className="room-header"><div><div className="room-header__meta"><span>Round {room.round_number ?? "—"}</span><span>{room.session_type.replaceAll("_", " ")}</span><span className={`status status--${room.status}`}>{room.status}</span></div><h1>{room.race_name}</h1><p>{room.circuit_name} · {room.country}</p></div><CircuitOutline circuitName={room.circuit_name} eventName={room.race_name} /><div className="session-progress"><span>{progressLabel}</span><b>{progressValue}</b>{!qualifying && room.total_laps != null && <small>/ {room.total_laps}</small>}</div></header>
    <div className="sticky-playback"><PlaybackControls room={room} playback={playback} busy={controlBusy} error={controlError} onReplay={runReplay} onControl={runControl} /></div>
    <AgentRoster agents={agents} selectedAgent={selectedAgent} onSelectAgent={setSelectedAgent} />
    <div className="room-layout">
      <MessageTimeline messages={messages} agents={agents} selectedAgent={selectedAgent} totalLaps={room.total_laps} sessionType={room.session_type} hasMore={nextCursor !== null} loadingMore={loadingMore} onSelectedAgentChange={setSelectedAgent} onLoadMore={loadMore} onInspectEvidence={setSelectedMessage} />
      <RoomContext slug={slug} detail={detail} playback={playback} />
    </div>
    <EvidenceDrawer slug={slug} message={selectedMessage} agent={evidenceAgent} onClose={closeEvidence} />
  </main>;
}
