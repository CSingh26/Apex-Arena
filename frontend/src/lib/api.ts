// SPDX-License-Identifier: AGPL-3.0-only
import type {
  EngineStatus,
  HealthResponse,
  SeasonCalendarSummary,
  SessionEventsResponse,
  SessionStateResponse,
} from "@/lib/types";

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

export function getEngineStatus(signal?: AbortSignal): Promise<EngineStatus> {
  return request<EngineStatus>("/api/v1/engine/status", signal);
}

export function getSessionEvents(
  sessionKey: string,
  signal?: AbortSignal,
): Promise<SessionEventsResponse> {
  return request<SessionEventsResponse>(
    `/api/v1/sessions/${encodeURIComponent(sessionKey)}/events`,
    signal,
  );
}

export function getSessionState(
  sessionKey: string,
  signal?: AbortSignal,
): Promise<SessionStateResponse> {
  return request<SessionStateResponse>(
    `/api/v1/sessions/${encodeURIComponent(sessionKey)}/state`,
    signal,
  );
}

export function sessionStreamUrl(sessionKey: string, lastSequenceNumber: number): string {
  return `${API_URL}/api/v1/stream/sessions/${encodeURIComponent(sessionKey)}?last_sequence_number=${lastSequenceNumber}`;
}

export { API_URL };
