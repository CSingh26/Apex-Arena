// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import { useSyncExternalStore } from "react";

import type { RaceRoomMode } from "@/lib/types";

import styles from "./race-room-mode-toggle.module.css";

export const RACE_ROOM_MODE_KEY = "apex-arena-race-room-mode";
const MODE_EVENT = "apex-arena-race-room-mode-change";

function subscribeMode(callback: () => void): () => void {
  window.addEventListener("storage", callback);
  window.addEventListener(MODE_EVENT, callback);
  return () => {
    window.removeEventListener("storage", callback);
    window.removeEventListener(MODE_EVENT, callback);
  };
}

function storedMode(): RaceRoomMode {
  return window.localStorage.getItem(RACE_ROOM_MODE_KEY) === "ANALYST" ? "ANALYST" : "FAN";
}

function defaultMode(): RaceRoomMode {
  return "FAN";
}

export function useRaceRoomMode(): {
  mode: RaceRoomMode;
  setMode: (mode: RaceRoomMode) => void;
} {
  const mode = useSyncExternalStore(subscribeMode, storedMode, defaultMode);
  const setMode = (next: RaceRoomMode) => {
    window.localStorage.setItem(RACE_ROOM_MODE_KEY, next);
    window.dispatchEvent(new Event(MODE_EVENT));
  };
  return { mode, setMode };
}

export function RaceRoomModeToggle({
  mode: controlledMode,
  onModeChange,
}: {
  mode?: RaceRoomMode;
  onModeChange?: (mode: RaceRoomMode) => void;
}) {
  const local = useRaceRoomMode();
  const mode = controlledMode ?? local.mode;
  const setMode = onModeChange ?? local.setMode;
  return (
    <div className={styles.toggle} role="group" aria-label="Race Room detail mode">
      {(["FAN", "ANALYST"] as const).map((value) => (
        <button
          key={value}
          type="button"
          aria-pressed={mode === value}
          onClick={() => setMode(value)}
        >
          {value === "FAN" ? "Fan" : "Analyst"}
        </button>
      ))}
    </div>
  );
}
