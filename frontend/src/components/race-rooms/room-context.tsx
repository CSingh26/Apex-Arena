// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import { getRoomDiagnostics } from "@/lib/api";
import type { RaceRoomDetailResponse, RoomDiagnostics, RoomPlayback } from "@/lib/types";

type RoomContextProps = {
  slug: string;
  detail: RaceRoomDetailResponse;
  playback: RoomPlayback;
};

function subscribeToContextSheet(callback: () => void): () => void {
  const query = window.matchMedia("(max-width: 860px)");
  query.addEventListener("change", callback);
  return () => query.removeEventListener("change", callback);
}

function contextSheetSnapshot(): boolean {
  return window.matchMedia("(max-width: 860px)").matches;
}

function formatDate(value: string | null): string {
  if (!value) return "Not recorded";
  return new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

export function RoomContext({ slug, detail, playback }: RoomContextProps) {
  const [contextOpen, setContextOpen] = useState(false);
  const compactViewport = useSyncExternalStore(subscribeToContextSheet, contextSheetSnapshot, () => true);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const sheetRef = useRef<HTMLElement>(null);
  const [diagnostics, setDiagnostics] = useState<RoomDiagnostics | null>(null);
  const [diagnosticsError, setDiagnosticsError] = useState<string | null>(null);
  const [loadingDiagnostics, setLoadingDiagnostics] = useState(false);
  const { room } = detail;

  useEffect(() => {
    if (!contextOpen) return;
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : triggerRef.current;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); setContextOpen(false); return; }
      if (event.key !== "Tab" || !sheetRef.current) return;
      const focusable = [...sheetRef.current.querySelectorAll<HTMLElement>("button:not([disabled]), summary, a[href], input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex='-1'])")];
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable.at(-1) ?? first;
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKeyDown);
    document.body.classList.add("drawer-open");
    window.requestAnimationFrame(() => closeRef.current?.focus());
    return () => { document.removeEventListener("keydown", onKeyDown); document.body.classList.remove("drawer-open"); previouslyFocused?.focus(); };
  }, [contextOpen]);

  const loadDiagnostics = () => {
    if (diagnostics || loadingDiagnostics) return;
    setLoadingDiagnostics(true);
    getRoomDiagnostics(slug).then(setDiagnostics).catch((reason: Error) => setDiagnosticsError(reason.message)).finally(() => setLoadingDiagnostics(false));
  };

  return <>
    <button ref={triggerRef} className="mobile-context-trigger control-button" type="button" aria-expanded={contextOpen} aria-controls="race-context-sheet" onClick={() => setContextOpen(true)}>Race context &amp; data <span aria-hidden>↑</span></button>
    {contextOpen && <button className="context-backdrop" type="button" aria-label="Close race context" onClick={() => setContextOpen(false)} />}
    <aside ref={sheetRef} id="race-context-sheet" className={`context-column ${contextOpen ? "context-column--open" : ""}`} aria-labelledby="race-context-title" role={contextOpen && compactViewport ? "dialog" : undefined} aria-modal={contextOpen && compactViewport || undefined} aria-hidden={compactViewport && !contextOpen || undefined} inert={compactViewport && !contextOpen || undefined}>
    <button ref={closeRef} className="context-close icon-button" type="button" aria-label="Close race context" onClick={() => setContextOpen(false)}>×</button>
    <section className="context-card">
      <p className="section-kicker">Room context</p><h2 id="race-context-title">Race data</h2>
      <dl className="context-list">
        <div><dt>Session</dt><dd>{room.session_type}</dd></div>
        <div><dt>Mode</dt><dd>{room.mode}</dd></div>
        <div><dt>Room status</dt><dd><span className={`status status--${room.status}`}>{room.status}</span></dd></div>
        <div><dt>Coverage</dt><dd>{room.source_availability.replaceAll("_", " ")}</dd></div>
        <div><dt>Telemetry quality</dt><dd>{room.telemetry_quality.replaceAll("_", " ")}</dd></div>
        <div><dt>Current event</dt><dd>#{playback.current_event_sequence}</dd></div>
        <div><dt>Last activity</dt><dd>{formatDate(room.last_event_at)}</dd></div>
      </dl>
      <p className="data-notice">{detail.data_notice}</p>
      {room.is_development && <p className="fixture-notice"><b>Validation fixture</b><span>This room uses deterministic synthetic race data. It does not represent a real event or championship result.</span></p>}
    </section>
    {detail.diagnostics_available && <details className="diagnostics-card" onToggle={(event) => { if (event.currentTarget.open) loadDiagnostics(); }}>
      <summary><span><span className="section-kicker">Development tools</span><b>Pipeline diagnostics</b></span><span aria-hidden>+</span></summary>
      {loadingDiagnostics && <div className="diagnostic-state" role="status"><span className="spinner" /> Reading pipeline state…</div>}
      {diagnosticsError && <div className="diagnostic-state diagnostic-state--error" role="alert">{diagnosticsError}</div>}
      {diagnostics && <div className="diagnostic-body">
        <dl className="diagnostic-counts"><div><dt>Raw</dt><dd>{diagnostics.raw_event_count}</dd></div><div><dt>Normalized</dt><dd>{diagnostics.normalized_event_count}</dd></div><div><dt>Snapshots</dt><dd>{diagnostics.snapshot_count}</dd></div></dl>
        <dl className="context-list"><div><dt>Provider</dt><dd>{diagnostics.provider_mode}</dd></div><div><dt>Connection</dt><dd>{diagnostics.connection_state}</dd></div><div><dt>Stream</dt><dd>{diagnostics.stream_state}</dd></div><div><dt>Ordering queue</dt><dd>{diagnostics.ordering_buffer_pending}</dd></div></dl>
        {!!Object.keys(diagnostics.discussion).length && <div className="diagnostic-metrics"><p className="section-kicker">Discussion engine</p>{Object.entries(diagnostics.discussion).map(([key, value]) => <span key={key}><b>{value}</b> {key.replaceAll("_", " ")}</span>)}</div>}
        {!!diagnostics.latest_events.length && <div className="diagnostic-events"><p className="section-kicker">Latest normalized events</p>{diagnostics.latest_events.slice(-5).reverse().map((event, index) => <div key={String(event.id ?? event.sequence_number ?? index)}><b>#{String(event.sequence_number ?? "—")}</b><span>{String(event.event_type ?? "event").replaceAll("_", " ")}</span><small>Lap {String(event.lap_number ?? "—")}</small></div>)}</div>}
      </div>}
    </details>}
    </aside>
  </>;
}
