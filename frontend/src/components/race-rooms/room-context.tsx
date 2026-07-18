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

function formatMetric(value: number | null, unit: string): string {
  if (value === null) return "—";
  return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(value)}${unit}`;
}

function windDirection(value: number | null): string {
  if (value === null) return "—";
  const compass = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
  return `${compass[Math.round(value / 45) % compass.length]} · ${Math.round(value)}°`;
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
    <button ref={triggerRef} className="mobile-context-trigger control-button" type="button" aria-expanded={contextOpen} aria-controls="race-context-sheet" onClick={() => setContextOpen(true)}>Session details <span aria-hidden>↑</span></button>
    {contextOpen && <button className="context-backdrop" type="button" aria-label="Close session details" onClick={() => setContextOpen(false)} />}
    <aside ref={sheetRef} id="race-context-sheet" className={`context-column ${contextOpen ? "context-column--open" : ""}`} aria-labelledby="race-context-title" role={contextOpen && compactViewport ? "dialog" : undefined} aria-modal={contextOpen && compactViewport || undefined} aria-hidden={compactViewport && !contextOpen || undefined} inert={compactViewport && !contextOpen || undefined}>
    <button ref={closeRef} className="context-close icon-button" type="button" aria-label="Close session details" onClick={() => setContextOpen(false)}>×</button>
    <section className="context-card">
      <p className="section-kicker">At a glance</p><h2 id="race-context-title">Session details</h2>
      <dl className="context-list">
        <div><dt>Session</dt><dd>{room.session_type}</dd></div>
        <div><dt>Room status</dt><dd><span className={`status status--${room.status}`}>{room.status}</span></dd></div>
        <div><dt>Coverage</dt><dd>{room.source_availability.replaceAll("_", " ")}</dd></div>
        <div><dt>Last activity</dt><dd>{formatDate(room.last_event_at)}</dd></div>
      </dl>
      <p className="data-notice">{detail.data_notice}</p>
      <details className="room-context-technical"><summary>Technical details</summary><dl className="context-list">
        <div><dt>Mode</dt><dd>{room.mode}</dd></div>
        <div><dt>Telemetry quality</dt><dd>{room.telemetry_quality.replaceAll("_", " ")}</dd></div>
        <div><dt>Current event</dt><dd>#{playback.current_event_sequence}</dd></div>
      </dl></details>
      {room.is_development && <p className="fixture-notice"><b>Validation fixture</b><span>This room uses deterministic synthetic race data. It does not represent a real event or championship result.</span></p>}
    </section>
    <section className="context-card circuit-dossier" aria-labelledby="circuit-dossier-title">
      <p className="section-kicker">Circuit intelligence</p>
      <h2 id="circuit-dossier-title">Track dossier</h2>
      <p className="circuit-dossier__name">{detail.circuit.circuit_name}</p>
      {!!detail.circuit.records.length && <dl className="circuit-records">
        {detail.circuit.records.map((record) => <div key={record.label}>
          <dt>{record.label}</dt><dd>{record.value}</dd>{record.detail && <small>{record.detail}</small>}
        </div>)}
      </dl>}
      <ul className="circuit-facts">
        {detail.circuit.facts.map((fact) => <li key={fact}>{fact}</li>)}
      </ul>
      {detail.circuit.source_url && <a className="context-source" href={detail.circuit.source_url} target="_blank" rel="noreferrer">Official circuit guide <span aria-hidden>↗</span></a>}
    </section>
    <section className={`context-card weather-card ${detail.weather.available ? "weather-card--live" : ""}`} aria-labelledby="weather-card-title">
      <div className="weather-card__heading">
        <div><p className="section-kicker">OpenF1 conditions</p><h2 id="weather-card-title">Track weather</h2></div>
        <span className="weather-card__signal" aria-label={detail.weather.available ? "Weather sample available" : "Weather pending"} />
      </div>
      {detail.weather.available && <>
        <p className="weather-card__sample">Latest sample · {formatDate(detail.weather.sampled_at)}</p>
        <dl className="weather-grid">
          <div><dt>Air</dt><dd>{formatMetric(detail.weather.air_temperature_c, "°C")}</dd></div>
          <div><dt>Track</dt><dd>{formatMetric(detail.weather.track_temperature_c, "°C")}</dd></div>
          <div><dt>Rainfall</dt><dd>{detail.weather.rainfall === null ? "—" : detail.weather.rainfall ? "Detected" : "None"}</dd></div>
          <div><dt>Humidity</dt><dd>{formatMetric(detail.weather.humidity_percent, "%")}</dd></div>
          <div><dt>Pressure</dt><dd>{formatMetric(detail.weather.pressure_mbar, " mbar")}</dd></div>
          <div><dt>Wind</dt><dd>{formatMetric(detail.weather.wind_speed_mps, " m/s")}</dd></div>
          <div className="weather-grid__wide"><dt>Direction</dt><dd>{windDirection(detail.weather.wind_direction_degrees)}</dd></div>
        </dl>
      </>}
      <p className="weather-card__notice">{detail.weather.notice}</p>
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
