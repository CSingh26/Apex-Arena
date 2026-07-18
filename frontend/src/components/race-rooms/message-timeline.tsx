// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { ConversationTimeline } from "@/components/race-rooms/conversation-timeline";
import type { AgentProfile, MessageTopic, MessageType, RoomMessage } from "@/lib/types";
import { roomMessageTime } from "@/lib/room-state";

const TOPICS: MessageTopic[] = ["strategy", "pace", "racecraft", "incident", "race_control", "weather", "pit_stop", "tyres", "championship", "summary", "session"];
const MESSAGE_TYPES: MessageType[] = ["observation", "analysis", "question", "reply", "agreement", "disagreement", "correction", "summary", "uncertainty_notice"];
const MAX_RENDERED_MESSAGES = 300;
const AGENT_SIDES: Record<string, "left" | "right" | "host"> = {
  "mira-vale": "right",
  "theo-voss": "left",
  "lena-cross": "right",
  "arjun-reyes": "left",
  nova: "host",
};

const MESSAGE_TYPE_LABELS: Record<MessageType, string> = {
  observation: "Call",
  analysis: "Read",
  question: "Challenge",
  reply: "Reply",
  agreement: "Backs it",
  disagreement: "Counterpoint",
  correction: "Data check",
  summary: "Room verdict",
  uncertainty_notice: "Caution",
};

type TimelineFilters = {
  agent: string;
  topic: string;
  type: string;
  lap: string;
};

export function filterRoomMessages(messages: RoomMessage[], filters: TimelineFilters): RoomMessage[] {
  return messages.filter((message) => (
    (filters.agent === "all" || message.agent_id === filters.agent)
    && (filters.topic === "all" || message.topic === filters.topic)
    && (filters.type === "all" || message.message_type === filters.type)
    && (!filters.lap || message.lap_number === Number(filters.lap))
  ));
}

type MessageTimelineProps = {
  messages: RoomMessage[];
  agents: AgentProfile[];
  selectedAgent: string;
  totalLaps: number | null;
  sessionType: string;
  hasMore: boolean;
  loadingMore: boolean;
  onSelectedAgentChange: (agent: string) => void;
  onLoadMore: () => void;
  onInspectEvidence: (message: RoomMessage) => void;
};

export function MessageTimeline({ messages, agents, selectedAgent, totalLaps, sessionType, hasMore, loadingMore, onSelectedAgentChange, onLoadMore, onInspectEvidence }: MessageTimelineProps) {
  const [topic, setTopic] = useState("all");
  const [type, setType] = useState("all");
  const [lap, setLap] = useState("");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [followingLatest, setFollowingLatest] = useState(true);
  const feedRef = useRef<HTMLDivElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const qualifying = sessionType.toUpperCase().includes("QUALIFY") || sessionType.toUpperCase().includes("SHOOTOUT");
  const filters = useMemo(() => ({ agent: selectedAgent, topic, type, lap }), [selectedAgent, topic, type, lap]);
  const filtered = useMemo(() => filterRoomMessages(messages, filters), [filters, messages]);
  const omitted = Math.max(0, filtered.length - MAX_RENDERED_MESSAGES);
  const visible = filtered.slice(-MAX_RENDERED_MESSAGES);
  const filtersActive = selectedAgent !== "all" || topic !== "all" || type !== "all" || lap !== "";
  const agentsById = useMemo(() => new Map(agents.map((agent) => [agent.id, agent])), [agents]);
  const messagesById = useMemo(() => new Map(messages.map((message) => [message.id, message])), [messages]);
  const clear = () => { onSelectedAgentChange("all"); setTopic("all"); setType("all"); setLap(""); };

  useEffect(() => {
    const url = new URL(window.location.href);
    const values = { agent: selectedAgent, topic, type, lap };
    Object.entries(values).forEach(([key, value]) => value && value !== "all" ? url.searchParams.set(key, value) : url.searchParams.delete(key));
    window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  }, [lap, selectedAgent, topic, type]);

  useEffect(() => {
    if (followingLatest) endRef.current?.scrollIntoView({ block: "end" });
  }, [followingLatest, visible.length]);

  const onFeedScroll = () => {
    const feed = feedRef.current;
    if (!feed) return;
    setFollowingLatest(feed.scrollHeight - feed.scrollTop - feed.clientHeight < 80);
  };

  const jumpToLatest = () => {
    setFollowingLatest(true);
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  };

  const jumpToSequence = (sequence: number) => {
    setFollowingLatest(false);
    feedRef.current?.querySelector<HTMLElement>(`[data-message-sequence="${sequence}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  return <section className="timeline-card" aria-labelledby="timeline-title">
    <div className="timeline-heading"><div><p className="section-kicker">What matters and why</p><h2 id="timeline-title">Session conversation</h2></div><button className="control-button control-button--quiet" type="button" onClick={jumpToLatest}>{followingLatest ? "At latest" : "Jump to latest"} <span aria-hidden>↓</span></button></div>
    <button className="filter-toggle" type="button" aria-expanded={filtersOpen} aria-controls="timeline-filters" onClick={() => setFiltersOpen(!filtersOpen)}><span>Filter conversation</span><span>{filtersActive ? "Filters active" : "All messages"} <b aria-hidden>{filtersOpen ? "−" : "+"}</b></span></button>
    {filtersOpen && <div id="timeline-filters" className="timeline-filters" aria-label="Filter messages">
      <label><span>Voice</span><select value={selectedAgent} onChange={(event) => onSelectedAgentChange(event.target.value)}><option value="all">All five agents</option>{agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.display_name}</option>)}</select></label>
      <label><span>Topic</span><select value={topic} onChange={(event) => setTopic(event.target.value)}><option value="all">All topics</option>{TOPICS.map((item) => <option key={item} value={item}>{item.replaceAll("_", " ")}</option>)}</select></label>
      <label><span>Message</span><select value={type} onChange={(event) => setType(event.target.value)}><option value="all">All types</option>{MESSAGE_TYPES.map((item) => <option key={item} value={item}>{item.replaceAll("_", " ")}</option>)}</select></label>
      {!qualifying && <label className="lap-filter"><span>Lap</span><input type="number" min="0" max={totalLaps ?? undefined} value={lap} placeholder="All" onChange={(event) => setLap(event.target.value)} /></label>}
      {filtersActive && <button className="clear-filters" type="button" onClick={clear}>Clear filters</button>}
      <span className="filter-count">{filtered.length} {filtered.length === 1 ? "message" : "messages"}</span>
    </div>}
    {hasMore && <div className="pagination-row"><button className="control-button" type="button" disabled={loadingMore} onClick={onLoadMore}>{loadingMore ? "Loading…" : "Load next messages"}</button><span>Messages are fetched in bounded pages.</span></div>}
    {omitted > 0 && <p className="window-notice">For performance, {omitted} earlier matching messages are hidden. Refine the filters to inspect them.</p>}
    <div ref={feedRef} className="message-feed" role="log" aria-label="Agent conversation" aria-live="polite" aria-relevant="additions" tabIndex={0} onScroll={onFeedScroll}>
      {visible.map((message) => {
        const agent = agentsById.get(message.agent_id);
        const parent = message.reply_to_message_id ? messagesById.get(message.reply_to_message_id) : undefined;
        const parentAgent = parent ? agentsById.get(parent.agent_id) : undefined;
        const side = AGENT_SIDES[message.agent_id] ?? (message.sequence % 2 ? "left" : "right");
        return <article className={`message message--${message.message_type} message--side-${side}`} data-testid="room-message" data-message-sequence={message.sequence} data-agent-id={message.agent_id} data-topic={message.topic} data-message-type={message.message_type} data-message-side={side} data-accent={agent?.ui_accent_key} key={message.id}>
          <div className="message__rail"><span className="agent-avatar" aria-hidden>{agent?.avatar_key ?? "AA"}</span><span className="timeline-line" /></div>
          <div className="message__body">
            <header className="message__meta"><strong>{agent?.display_name ?? "Race Room"}</strong><span>{agent?.role ?? "Room voice"}</span><span>{message.session_phase ?? (qualifying ? "Session" : message.lap_number == null ? "Session" : `Lap ${message.lap_number}`)}</span></header>
            <div className="message__labels"><span className={`message-type message-type--${message.message_type}`}>{MESSAGE_TYPE_LABELS[message.message_type]}</span><span>{message.topic.replaceAll("_", " ")}</span>{message.reply_to_message_id && <span className="reply-label">↳ Replying to {parentAgent?.display_name ?? "an earlier message"}</span>}</div>
            <p>{message.content}</p>
            <footer className="message__footer"><button className="evidence-button" type="button" aria-label={`See the data behind ${agent?.display_name ?? "this"} message`} onClick={() => onInspectEvidence(message)}><span className={`evidence-dot evidence-dot--${message.evidence_status}`} aria-hidden /> See the data <span className="sr-only">Evidence status: {message.evidence_status}. Message time: {roomMessageTime(message)}.</span></button></footer>
          </div>
        </article>;
      })}
      {!visible.length && <div className="room-state room-state--quiet"><span aria-hidden>◌</span><b>{messages.length ? "No messages match these filters." : "The room is waiting for lights out."}</b><p>{messages.length ? "Clear a filter to return to the conversation." : "Start the replay and the five agents will respond to meaningful race events."}</p>{filtersActive && <button className="control-button" type="button" onClick={clear}>Clear filters</button>}</div>}
      <div ref={endRef} />
    </div>
    <ConversationTimeline messages={filtered} totalLaps={totalLaps} sessionType={sessionType} onSelect={jumpToSequence} />
  </section>;
}
