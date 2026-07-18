// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import { useEffect, useSyncExternalStore } from "react";

type Theme = "dark" | "light";
const THEME_STORAGE_KEY = "apex-arena-theme";
const THEME_CHANGE_EVENT = "apex-arena-theme-change";

function preferredTheme(): Theme {
  const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
  if (stored === "dark" || stored === "light") return stored;
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function subscribeToTheme(callback: () => void): () => void {
  const media = window.matchMedia("(prefers-color-scheme: light)");
  window.addEventListener("storage", callback);
  window.addEventListener(THEME_CHANGE_EVENT, callback);
  media.addEventListener("change", callback);
  return () => {
    window.removeEventListener("storage", callback);
    window.removeEventListener(THEME_CHANGE_EVENT, callback);
    media.removeEventListener("change", callback);
  };
}

export function ThemeToggle() {
  const theme = useSyncExternalStore(subscribeToTheme, preferredTheme, () => "dark");
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  const toggle = () => {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    window.localStorage.setItem(THEME_STORAGE_KEY, next);
    window.dispatchEvent(new Event(THEME_CHANGE_EVENT));
  };
  return <button suppressHydrationWarning className="theme-toggle" type="button" onClick={toggle} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`} title={`Use ${theme === "dark" ? "light" : "dark"} theme`}><span aria-hidden>{theme === "dark" ? "☼" : "◐"}</span></button>;
}
