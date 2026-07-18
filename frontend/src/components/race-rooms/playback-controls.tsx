// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import { useState } from "react";

import type { PlaybackAction, RaceRoom, ReplayAction, RoomPlayback } from "@/lib/types";

const SPEEDS = [0.5, 1, 2, 4, 8] as const;

type PlaybackControlsProps = {
  room: RaceRoom;
  playback: RoomPlayback;
  busy: boolean;
  error: string | null;
  onReplay: (action: ReplayAction) => Promise<void>;
  onControl: (action: PlaybackAction) => Promise<void>;
};

export function PlaybackControls({ room, playback, busy, error, onReplay, onControl }: PlaybackControlsProps) {
  const [lapTarget, setLapTarget] = useState(String(playback.current_lap ?? 1));
  const [sequenceTarget, setSequenceTarget] = useState(String(playback.current_event_sequence));
  const [sessionTimeTarget, setSessionTimeTarget] = useState("0");
  const [seekOpen, setSeekOpen] = useState(false);
  const normalizedSessionType = room.session_type.toUpperCase().replaceAll(" ", "_");
  const qualifying = normalizedSessionType.includes("QUALIFY") || normalizedSessionType.includes("SHOOTOUT");
  const sprintQualifying = normalizedSessionType.includes("SPRINT") || normalizedSessionType.includes("SHOOTOUT");
  const phases = sprintQualifying ? ["SQ1", "SQ2", "SQ3"] : ["Q1", "Q2", "Q3"];

  const toggleSeek = () => {
    if (!seekOpen) {
      setLapTarget(String(playback.current_lap ?? 1));
      setSequenceTarget(String(playback.current_event_sequence));
    }
    setSeekOpen((value) => !value);
  };

  const hasStarted = playback.started_at !== null || playback.current_event_sequence > 0;
  const complete = room.status === "completed";
  return <section className="playback-bar" data-testid="playback-controls" aria-label="Replay controls" aria-busy={busy}>
    <div className="playback-bar__primary">
      {!hasStarted && <button className="control-button control-button--primary" data-testid="start-replay" type="button" disabled={busy} onClick={() => onReplay("start")}><span aria-hidden>▶</span> Start replay</button>}
      {hasStarted && !complete && <button className="control-button control-button--primary" data-testid="toggle-playback" type="button" disabled={busy} onClick={() => onControl({ action: playback.is_paused ? "resume" : "pause" })}>{playback.is_paused ? <><span aria-hidden>▶</span> Resume</> : <><span aria-hidden>Ⅱ</span> Pause</>}</button>}
      {hasStarted && <button className="control-button" data-testid="restart-replay" type="button" disabled={busy} onClick={() => onReplay("restart")}><span aria-hidden>↺</span> Restart</button>}
      <label className="speed-control"><span>Speed</span><select aria-label="Playback speed" disabled={busy || !hasStarted} value={playback.playback_speed} onChange={(event) => onControl({ action: "set_speed", playback_speed: Number(event.target.value) as 0.5 | 1 | 2 | 4 | 8 })}>{SPEEDS.map((speed) => <option key={speed} value={speed}>{speed}×</option>)}</select></label>
      <button className="control-button control-button--quiet" type="button" aria-expanded={seekOpen} aria-controls="replay-seek-controls" disabled={!hasStarted} onClick={toggleSeek}>Seek <span aria-hidden>{seekOpen ? "▴" : "▾"}</span></button>
    </div>
    <div className="playback-progress" aria-label={qualifying ? `Replay in ${room.current_phase ?? "the qualifying session"}` : playback.current_lap == null ? "Replay progress is not available yet" : `Replay at lap ${playback.current_lap}`}>
      <span className="playback-progress__fill" style={{ width: `${qualifying ? Math.max(0, (phases.indexOf(room.current_phase ?? "") + 1) / phases.length * 100) : room.total_laps && playback.current_lap ? Math.min(100, playback.current_lap / room.total_laps * 100) : 0}%` }} />
    </div>
    <div className="playback-bar__readout" aria-live="polite">{qualifying ? <span>Phase <b>{room.current_phase ?? "Session"}</b></span> : <span>{playback.current_lap == null ? "Lap data pending" : <>Lap <b>{playback.current_lap}</b>{room.total_laps ? ` / ${room.total_laps}` : ""}</>}</span>}<span className={`playback-state playback-state--${playback.is_paused ? "paused" : "running"}`} data-testid="playback-status">{complete ? "Replay complete" : playback.is_paused ? "Paused" : "Running"}</span></div>
    {seekOpen && <div id="replay-seek-controls" className="playback-seek">
      {qualifying ? <>
        <div className="phase-seek" aria-label="Qualifying phases">{phases.map((phase) => <button className="control-button" type="button" disabled={busy || room.phase_boundaries_available === false} onClick={() => onControl({ action: "seek_to_phase", phase })} key={phase}>{phase}</button>)}</div>
        <label><span>Session time (seconds)</span><input type="number" min="0" value={sessionTimeTarget} onChange={(event) => setSessionTimeTarget(event.target.value)} /></label><button className="control-button" type="button" disabled={busy || !sessionTimeTarget} onClick={() => onControl({ action: "seek_to_session_time", session_time: Number(sessionTimeTarget) })}>Go to time</button>
      </> : <><label><span>Lap</span><input type="number" min="1" max={room.total_laps ?? undefined} value={lapTarget} onChange={(event) => setLapTarget(event.target.value)} /></label><button className="control-button" type="button" disabled={busy || !lapTarget} onClick={() => onControl({ action: "seek_to_lap", lap_number: Number(lapTarget) })}>Go to lap</button></>}
      <label><span>Event sequence</span><input type="number" min="0" value={sequenceTarget} onChange={(event) => setSequenceTarget(event.target.value)} /></label><button className="control-button" type="button" disabled={busy || !sequenceTarget} onClick={() => onControl({ action: "seek_to_sequence", sequence: Number(sequenceTarget) })}>Go to event</button>
    </div>}
    {error && <p className="control-error" role="alert">{error}</p>}
  </section>;
}
