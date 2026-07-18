// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import type { RoomMessage } from "@/lib/types";

type ConversationTimelineProps = {
  messages: RoomMessage[];
  totalLaps: number | null;
  sessionType: string;
  onSelect: (sequence: number) => void;
};

type TimelineMoment = {
  sequence: number;
  label: string;
  topic: string;
  position: number;
};

const IMPORTANT_TYPES = new Set(["question", "disagreement", "correction", "summary", "uncertainty_notice"]);

function momentsFor(messages: RoomMessage[], totalLaps: number | null, qualifying: boolean): TimelineMoment[] {
  const candidates = messages.filter((message) => (
    message.session_phase || message.lap_number != null || IMPORTANT_TYPES.has(message.message_type)
  ));
  const unique = new Map<string, RoomMessage>();
  for (const message of candidates) {
    const key = qualifying
      ? message.session_phase ?? `sequence-${message.sequence}`
      : message.lap_number == null ? `sequence-${message.sequence}` : `lap-${message.lap_number}`;
    unique.set(key, message);
  }
  const selected = [...unique.values()].slice(-10);
  return selected.map((message, index) => {
    const position = !qualifying && totalLaps && message.lap_number != null
      ? Math.min(100, Math.max(0, (message.lap_number / totalLaps) * 100))
      : selected.length === 1 ? 50 : (index / Math.max(1, selected.length - 1)) * 100;
    return {
      sequence: message.sequence,
      label: message.session_phase ?? (message.lap_number == null ? `#${message.sequence}` : `L${message.lap_number}`),
      topic: message.topic.replaceAll("_", " "),
      position,
    };
  });
}

export function ConversationTimeline({ messages, totalLaps, sessionType, onSelect }: ConversationTimelineProps) {
  const qualifying = sessionType.toUpperCase().includes("QUALIFY") || sessionType.toUpperCase().includes("SHOOTOUT");
  const moments = momentsFor(messages, totalLaps, qualifying);
  return <section className="conversation-timeline" aria-labelledby="conversation-timeline-title">
    <header><div><p className="section-kicker">Conversation map</p><h3 id="conversation-timeline-title">Session timeline</h3></div><span>{moments.length} key {moments.length === 1 ? "moment" : "moments"}</span></header>
    {moments.length ? <div className="conversation-timeline__rail" aria-label="Jump to a key conversation moment">
      <span aria-hidden />
      {moments.map((moment) => <button type="button" style={{ left: `${moment.position}%` }} onClick={() => onSelect(moment.sequence)} aria-label={`Jump to ${moment.label}, ${moment.topic}`} key={moment.sequence}><i aria-hidden /><b>{moment.label}</b><small>{moment.topic}</small></button>)}
    </div> : <p className="conversation-timeline__empty">Key laps and phase changes will appear here as the room reacts.</p>}
  </section>;
}
