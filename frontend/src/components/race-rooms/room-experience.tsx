// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { AppNavigation } from "@/components/navigation/app-navigation";
import { AgentRoster } from "@/components/race-rooms/agent-roster";
import { CircuitOutline } from "@/components/race-rooms/circuit-outline";
import { EvidenceDrawer } from "@/components/race-rooms/evidence-drawer";
import { LiveCommandCenter } from "@/components/race-rooms/live-command-center";
import { MessageTimeline } from "@/components/race-rooms/message-timeline";
import { PlaybackControls } from "@/components/race-rooms/playback-controls";
import { RoomContext } from "@/components/race-rooms/room-context";
import { getRaceRoom, getRoomMessages, roomStreamUrl, startRoomReplay, updateRoomPlayback } from "@/lib/api";
import { appRoutes } from "@/lib/app-paths";
import { mergeRoomMessages } from "@/lib/room-state";
import type { MessageTopic, MessageType, PlaybackAction, RaceRoomDetailResponse, ReplayAction, RoomMessage, RoomMode, RoomPlayback, RoomStatus } from "@/lib/types";

import styles from "./race-rooms-revamp.module.css";

type ConnectionState = "connecting" | "live" | "reconnecting" | "degraded";
const ROOM_STATUSES = new Set<RoomStatus>(["pending", "ingesting", "ready", "live", "replaying", "paused", "completed", "failed", "unavailable"]);
const ROOM_MODES = new Set<RoomMode>(["live", "replay", "archived"]);
const TERMINAL_ROOM_STATUSES = new Set<RoomStatus>(["completed", "failed", "unavailable"]);
const MESSAGE_TOPICS = new Set<MessageTopic>(["strategy", "pace", "racecraft", "incident", "race_control", "weather", "pit_stop", "tyres", "championship", "summary", "session"]);
const MESSAGE_TYPES = new Set<MessageType>(["observation", "analysis", "question", "reply", "agreement", "disagreement", "correction", "summary", "uncertainty_notice"]);
const MESSAGE_CONFIDENCE = new Set(["low", "medium", "high"]);
const EVIDENCE_STATUSES = new Set(["grounded", "partial", "unavailable"]);
const IMPORTANT_TOPICS = new Set(["incident", "race_control", "pit_stop", "weather"]);

type StreamRecord = Record<string, unknown>;
type RoomStatusPayload = {
  status: RoomStatus;
  mode?: RoomMode;
  current_lap?: number | null;
};

function friendlyRoomError(reason: unknown): string {
  if (reason instanceof TypeError) return "We couldn’t reach the race service. Check your connection and try again.";
  const message = reason instanceof Error ? reason.message.toLowerCase() : "";
  if (message.includes("404") || message.includes("not found")) return "This session room is not available yet.";
  if (message.includes("telemetry") || message.includes("provider")) return "The timing provider is temporarily unavailable. The room can be retried shortly.";
  return "This room is temporarily unavailable. Try again in a moment.";
}

function streamRecord(event: Event): StreamRecord | null {
  try {
    const payload = JSON.parse((event as MessageEvent).data) as unknown;
    return payload !== null && typeof payload === "object" && !Array.isArray(payload)
      ? payload as StreamRecord
      : null;
  } catch {
    return null;
  }
}

function isFiniteInteger(value: unknown, minimum = 0): value is number {
  return typeof value === "number" && Number.isFinite(value) && Number.isInteger(value) && value >= minimum;
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function hasMatchingOptionalIdentity(payload: StreamRecord, roomId: string, sessionKey: string | null): boolean {
  if ("room_id" in payload && payload.room_id !== roomId) return false;
  if ("session_key" in payload && payload.session_key !== sessionKey) return false;
  return true;
}

function roomMessagePayload(event: Event, roomId: string, sessionKey: string | null): RoomMessage | null {
  const payload = streamRecord(event);
  if (!payload || !hasMatchingOptionalIdentity(payload, roomId, sessionKey)) return null;
  if (
    typeof payload.id !== "string"
    || payload.room_id !== roomId
    || typeof payload.agent_id !== "string"
    || !isFiniteInteger(payload.sequence, 1)
    || !(payload.lap_number === null || isFiniteInteger(payload.lap_number))
    || !(payload.session_time === null || (typeof payload.session_time === "number" && Number.isFinite(payload.session_time)))
    || !isNullableString(payload.wall_time)
    || !MESSAGE_TOPICS.has(payload.topic as MessageTopic)
    || !MESSAGE_TYPES.has(payload.message_type as MessageType)
    || typeof payload.content !== "string"
    || !MESSAGE_CONFIDENCE.has(payload.confidence as string)
    || !EVIDENCE_STATUSES.has(payload.evidence_status as string)
    || !isNullableString(payload.reply_to_message_id)
    || !isNullableString(payload.trigger_event_id)
    || !isNullableString(payload.trigger_snapshot_id)
    || typeof payload.generated_by !== "string"
    || !isNullableString(payload.model_name)
    || typeof payload.prompt_version !== "string"
    || typeof payload.created_at !== "string"
    || !(payload.session_phase === undefined || isNullableString(payload.session_phase))
  ) return null;
  return payload as RoomMessage;
}

function playbackPayload(event: Event, roomId: string, sessionKey: string | null): RoomPlayback | null {
  const payload = streamRecord(event);
  if (!payload || !hasMatchingOptionalIdentity(payload, roomId, sessionKey)) return null;
  if (
    payload.room_id !== roomId
    || !isFiniteInteger(payload.current_event_sequence)
    || !isFiniteInteger(payload.current_message_sequence)
    || !(payload.current_lap === null || isFiniteInteger(payload.current_lap))
    || !(typeof payload.playback_speed === "number" && Number.isFinite(payload.playback_speed) && payload.playback_speed >= 0.5 && payload.playback_speed <= 8)
    || typeof payload.is_paused !== "boolean"
    || !isNullableString(payload.started_at)
    || typeof payload.updated_at !== "string"
    || !isNullableString(payload.session_clock)
  ) return null;
  return payload as RoomPlayback;
}

function roomStatusPayload(event: Event, roomId: string, sessionKey: string | null): RoomStatusPayload | null {
  const payload = streamRecord(event);
  if (!payload || !hasMatchingOptionalIdentity(payload, roomId, sessionKey)) return null;
  if (!ROOM_STATUSES.has(payload.status as RoomStatus)) return null;
  if ("mode" in payload && !ROOM_MODES.has(payload.mode as RoomMode)) return null;
  if ("current_lap" in payload && !(payload.current_lap === null || isFiniteInteger(payload.current_lap))) return null;
  return {
    status: payload.status as RoomStatus,
    ...(payload.mode === undefined ? {} : { mode: payload.mode as RoomMode }),
    ...(payload.current_lap === undefined ? {} : { current_lap: payload.current_lap as number | null }),
  };
}

function RoomLoadingState() {
  return <main id="main-content" className={`room-page track-grid ${styles.room}`}><div className={styles.roomSkeleton} role="status" aria-label="Joining the race room"><span className={styles.skeletonEyebrow} /><span className={styles.skeletonTitle} /><span className={styles.skeletonControls} /><div aria-hidden><span /><span /></div><p>Joining the race room…</p></div></main>;
}

export function RoomExperience({ slug }: { slug: string }) {
  const [detail, setDetail] = useState<RaceRoomDetailResponse | null>(null);
  const [messages, setMessages] = useState<RoomMessage[]>([]);
  const [playback, setPlayback] = useState<RoomPlayback | null>(null);
  const [selectedAgent, setSelectedAgent] = useState("all");
  const [selectedDriver, setSelectedDriver] = useState<number | null>(null);
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
  const [terminalReconciliation, setTerminalReconciliation] = useState(false);
  const lastSequenceRef = useRef(0);
  const roomUpdateRef = useRef(0);
  const sessionKeyRef = useRef<string | null>(null);

  useEffect(() => {
    sessionKeyRef.current = detail?.room.session_key ?? null;
  }, [detail?.room.session_key]);

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
      setTerminalReconciliation(false);
      setMessages([]);
      lastSequenceRef.current = 0;
      mergeMessages(feed.messages);
      setNextCursor(feed.next_cursor);
    }).catch((reason: Error) => {
      if (active && reason.name !== "AbortError") setError(friendlyRoomError(reason));
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; controller.abort(); };
  }, [mergeMessages, reloadKey, slug]);

  const roomId = detail?.room.id;
  const liveRoom = detail?.room.status === "live";
  useEffect(() => {
    if ((!liveRoom && !terminalReconciliation) || !roomId) return;
    const controller = new AbortController();
    let disposed = false;
    let timer: number | null = null;
    const refresh = async () => {
      const requestRoomUpdate = roomUpdateRef.current;
      let reconciledTerminal = false;
      try {
        const latest = await getRaceRoom(slug, controller.signal);
        if (!disposed && latest.room.id === roomId) {
          const authoritativeTerminal = terminalReconciliation
            && latest.room.status === "completed"
            && latest.room.mode === "archived";
          if (authoritativeTerminal) {
            reconciledTerminal = true;
            setDetail((current) => current?.room.id === roomId ? latest : current);
            setPlayback(latest.playback);
            setTerminalReconciliation(false);
          } else if (!terminalReconciliation && roomUpdateRef.current === requestRoomUpdate) {
            reconciledTerminal = TERMINAL_ROOM_STATUSES.has(latest.room.status);
            setDetail((current) => current?.room.id === roomId ? latest : current);
            if (reconciledTerminal) setPlayback(latest.playback);
          } else {
            setDetail((current) => {
              if (!current || current.room.id !== roomId) return current;
              return {
                ...latest,
                room: {
                  ...latest.room,
                  status: current.room.status,
                  mode: current.room.mode,
                  current_lap: current.room.current_lap,
                },
              };
            });
          }
        }
      } catch {
        // Keep last known data while the shared backend ingestion reconnects.
      } finally {
        if (!disposed && !reconciledTerminal) timer = window.setTimeout(refresh, 15_000);
      }
    };
    timer = window.setTimeout(refresh, terminalReconciliation ? 0 : 15_000);
    return () => {
      disposed = true;
      controller.abort();
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [liveRoom, roomId, slug, terminalReconciliation]);

  useEffect(() => {
    if (!roomId) return;
    let disposed = false;
    let source: EventSource | null = null;
    let retryTimer: number | null = null;
    let retryAttempt = 0;

    const connect = () => {
      if (disposed) return;
      retryTimer = null;
      setConnection(retryAttempt ? "reconnecting" : "connecting");
      const nextSource = new EventSource(roomStreamUrl(slug, lastSequenceRef.current));
      source = nextSource;
      const isCurrent = () => !disposed && source === nextSource;
      nextSource.addEventListener("open", () => {
        if (!isCurrent()) return;
        retryAttempt = 0;
        setConnection("live");
      });
      nextSource.addEventListener("room_message", (event) => {
        if (!isCurrent()) return;
        const message = roomMessagePayload(event, roomId, sessionKeyRef.current);
        if (!message) return;
        mergeMessages([message]);
      });
      nextSource.addEventListener("playback_state", (event) => {
        if (!isCurrent()) return;
        const nextPlayback = playbackPayload(event, roomId, sessionKeyRef.current);
        if (nextPlayback) setPlayback(nextPlayback);
      });
      nextSource.addEventListener("room_status", (event) => {
        if (!isCurrent()) return;
        const payload = roomStatusPayload(event, roomId, sessionKeyRef.current);
        if (!payload) return;
        roomUpdateRef.current += 1;
        setTerminalReconciliation(payload.status === "completed");
        setDetail((current) => current ? {
          ...current,
          room: {
            ...current.room,
            status: payload.status,
            mode: payload.mode ?? current.room.mode,
            current_lap: payload.current_lap === undefined ? current.room.current_lap : payload.current_lap,
          },
        } : current);
      });
      nextSource.addEventListener("connection_status", (event) => {
        if (!isCurrent()) return;
        const payload = streamRecord(event);
        if (!payload) return;
        if (payload.status === "degraded") setConnection("degraded");
      });
      nextSource.addEventListener("error", () => {
        if (!isCurrent()) return;
        nextSource.close();
        source = null;
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
    } catch {
      setControlError("That replay control didn’t respond. Your current position has been preserved.");
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
    } catch {
      setControlError("The replay couldn’t start yet. Wait a moment and try again.");
    } finally { setControlBusy(false); }
  }, [slug]);

  const loadMore = useCallback(async () => {
    if (nextCursor == null || loadingMore) return;
    setLoadingMore(true);
    try {
      const feed = await getRoomMessages(slug, `after_sequence=${nextCursor}&limit=100`);
      mergeMessages(feed.messages);
      setNextCursor(feed.next_cursor);
    } catch {
      setControlError("More conversation couldn’t be loaded. Try again when your connection is stable.");
    } finally { setLoadingMore(false); }
  }, [loadingMore, mergeMessages, nextCursor, slug]);

  const closeEvidence = useCallback(() => setSelectedMessage(null), []);

  if (loading) return <RoomLoadingState />;
  if (error || !detail || !playback) return <main id="main-content" className={`room-page track-grid ${styles.room}`}><div className="room-state room-state--error room-state--centered" role="alert"><span aria-hidden>!</span><b>Room unavailable</b><p>{error ?? "This session room returned incomplete data. Try again shortly."}</p><div><button className="control-button" type="button" onClick={() => { setLoading(true); setError(null); setReloadKey((value) => value + 1); }}>Try again</button><Link className="control-button" href={appRoutes.rooms}>All Race Rooms</Link></div></div></main>;

  const { room, agents } = detail;
  const evidenceAgent = selectedMessage ? agents.find((agent) => agent.id === selectedMessage.agent_id) : undefined;
  const qualifying = room.session_type.toUpperCase().includes("QUALIFY") || room.session_type.toUpperCase().includes("SHOOTOUT");
  const progressLabel = qualifying ? "Current phase" : room.status === "live" ? "Current lap" : "Replay lap";
  const progressValue = qualifying ? (room.current_phase ?? "Session") : (playback.current_lap ?? room.current_lap ?? "—");
  const latestSignal = [...messages].reverse().find((message) => IMPORTANT_TOPICS.has(message.topic) || ["summary", "correction", "uncertainty_notice"].includes(message.message_type));
  return <main id="main-content" className={`room-page track-grid ${styles.room}`}>
    <AppNavigation contextLabel={`${room.race_name} · ${room.session_type}`} connection={connection} />
    <Link className="room-breadcrumb" href={appRoutes.rooms}><span aria-hidden>←</span> All Race Rooms</Link>
    <header className="room-header"><div><div className="room-header__meta"><span>Round {room.round_number ?? "—"}</span><span>{room.session_type.replaceAll("_", " ")}</span><span className={`status status--${room.status}`}>{room.status}</span></div><h1>{room.race_name}</h1><p>{room.circuit_name} · {room.country}</p></div><CircuitOutline circuitName={room.circuit_name} eventName={room.race_name} /><div className="session-progress"><span>{progressLabel}</span><b>{progressValue}</b>{!qualifying && room.total_laps != null && <small>/ {room.total_laps}</small>}</div></header>
    {connection !== "live" && <div className={styles.connectionNotice} role="status"><span aria-hidden /> <b>{connection === "degraded" ? "Live updates are delayed" : "Reconnecting to live updates"}</b><p>The conversation already loaded remains available while the connection recovers.</p></div>}
    <div className="sticky-playback"><PlaybackControls room={room} playback={playback} busy={controlBusy} error={controlError} onReplay={runReplay} onControl={runControl} /></div>
    <LiveCommandCenter sessionKey={room.session_key} circuitName={room.circuit_name} eventName={room.race_name} playbackSequence={playback.current_event_sequence} sessionClock={playback.session_clock} selectedDriver={selectedDriver} onSelectDriver={setSelectedDriver} initialIntelligence={detail.intelligence} sourceAvailability={room.source_availability} locationDataMode={room.mode === "live" && room.status === "live" ? "mutable" : "immutable"} />
    {latestSignal && <aside className={styles.raceSignal} aria-label="Latest important room update"><span>{latestSignal.session_phase ?? (latestSignal.lap_number == null ? "Session update" : `Lap ${latestSignal.lap_number}`)}</span><div><b>{latestSignal.topic.replaceAll("_", " ")}</b><p>{latestSignal.content}</p></div><a href="#timeline-title">Open conversation <span aria-hidden>↓</span></a></aside>}
    <AgentRoster agents={agents} selectedAgent={selectedAgent} onSelectAgent={setSelectedAgent} />
    <div className="room-layout">
      <MessageTimeline messages={messages} agents={agents} selectedAgent={selectedAgent} totalLaps={room.total_laps} sessionType={room.session_type} hasMore={nextCursor !== null} loadingMore={loadingMore} onSelectedAgentChange={setSelectedAgent} onLoadMore={loadMore} onInspectEvidence={setSelectedMessage} />
      <RoomContext slug={slug} detail={detail} playback={playback} />
    </div>
    <EvidenceDrawer slug={slug} message={selectedMessage} agent={evidenceAgent} onClose={closeEvidence} />
  </main>;
}
