// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import { useEffect, useRef, useState } from "react";

import { getMessageEvidence } from "@/lib/api";
import type { AgentProfile, MessageEvidenceResponse, RoomMessage } from "@/lib/types";

type EvidenceDrawerProps = {
  slug: string;
  message: RoomMessage | null;
  agent?: AgentProfile;
  onClose: () => void;
};

function valueLabel(value: string | number | null, fallback: string): string {
  return value == null || value === "" ? fallback : String(value);
}

export function EvidenceDrawer({ slug, message, agent, onClose }: EvidenceDrawerProps) {
  const [payload, setPayload] = useState<MessageEvidenceResponse | null>(null);
  const [error, setError] = useState<{ messageId: string; text: string } | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!message) return;
    let active = true;
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    getMessageEvidence(slug, message.id).then((data) => { if (active) setPayload(data); }).catch((reason: Error) => { if (active) setError({ messageId: message.id, text: reason.message }); });
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); onClose(); return; }
      if (event.key !== "Tab" || !drawerRef.current) return;
      const focusable = [...drawerRef.current.querySelectorAll<HTMLElement>("button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex='-1'])")];
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable.at(-1) ?? first;
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKeyDown);
    document.body.classList.add("drawer-open");
    window.requestAnimationFrame(() => closeRef.current?.focus());
    return () => { active = false; document.removeEventListener("keydown", onKeyDown); document.body.classList.remove("drawer-open"); previouslyFocused?.focus(); };
  }, [message, onClose, slug]);

  if (!message) return null;
  const currentPayload = payload?.message_id === message.id ? payload : null;
  const currentError = error?.messageId === message.id ? error.text : null;
  return <div className="drawer-layer">
    <button className="drawer-backdrop" type="button" aria-label="Close evidence panel" onClick={onClose} />
    <aside ref={drawerRef} className="evidence-drawer" data-testid="evidence-drawer" role="dialog" aria-modal="true" aria-labelledby="evidence-title">
      <header className="evidence-drawer__header"><div><p className="section-kicker">Grounding trace</p><h2 id="evidence-title">Message evidence</h2></div><button ref={closeRef} className="icon-button" type="button" aria-label="Close evidence panel" onClick={onClose}>×</button></header>
      <div className="evidence-quote"><span className="agent-avatar" data-accent={agent?.ui_accent_key} aria-hidden>{agent?.avatar_key ?? "AA"}</span><div><b>{agent?.display_name ?? "Race Room"}</b><p>{message.content}</p></div></div>
      <dl className="evidence-summary">
        <div><dt>Grounding</dt><dd data-quality={message.evidence_status}>{message.evidence_status}</dd></div>
        <div><dt>Confidence</dt><dd>{currentPayload?.confidence ?? message.confidence}</dd></div>
        <div><dt>Generation</dt><dd>{currentPayload?.generation_mode ?? message.generated_by}</dd></div>
        <div><dt>Snapshot</dt><dd>{currentPayload?.snapshot_reference ?? "Event state"}</dd></div>
      </dl>
      {!currentPayload && !currentError && <div className="drawer-state" role="status"><span className="spinner" /> Loading source trace…</div>}
      {currentError && <div className="drawer-state drawer-state--error" role="alert"><b>Evidence unavailable</b><p>{currentError}</p></div>}
      {currentPayload && <>
        {currentPayload.trigger_event && <section className="evidence-section"><p className="section-kicker">Trigger event</p><div className="trigger-card"><b>Event #{currentPayload.trigger_event.event_sequence ?? "—"}</b><span>Lap {currentPayload.trigger_event.lap_number ?? "—"}</span><small>{currentPayload.trigger_event.source_provider} · {currentPayload.trigger_event.event_id}</small></div></section>}
        <section className="evidence-section"><p className="section-kicker">Data quality</p>{currentPayload.data_quality_flags.length ? <div className="quality-flags">{currentPayload.data_quality_flags.map((flag) => <span key={flag}>{flag}</span>)}</div> : <p className="muted-copy">No additional quality flags were reported.</p>}</section>
        <section className="evidence-section"><p className="section-kicker">Supporting facts</p>{currentPayload.evidence.length ? <div className="evidence-list">{currentPayload.evidence.map((item) => <article key={item.id}><div><b>{item.metric_name ?? item.evidence_type.replaceAll("_", " ")}</b><span>{item.source_provider}</span></div><p>{valueLabel(item.metric_value, item.source_reference)} {item.unit ?? ""}</p><small>{item.evidence_key} · {item.source_reference}</small></article>)}</div> : <p className="muted-copy">This message has no detailed telemetry facts attached.</p>}</section>
      </>}
    </aside>
  </div>;
}
