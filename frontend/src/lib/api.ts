// SPDX-License-Identifier: AGPL-3.0-only
import type { HealthResponse, SeasonCalendarSummary } from "@/lib/types";

const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

async function request<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) {
    throw new Error(`API request failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return request<HealthResponse>("/health", signal);
}

export function getSeason(signal?: AbortSignal): Promise<SeasonCalendarSummary> {
  return request<SeasonCalendarSummary>("/api/v1/season/2026", signal);
}

export { API_URL };
