// SPDX-License-Identifier: AGPL-3.0-only
import type {
  EngineStatus,
  HealthResponse,
  SeasonCalendarSummary,
  SessionEventsResponse,
  SessionStateResponse,
  MessageEvidenceResponse,
  RaceRoomDetailResponse,
  RaceRoomEventsResponse,
  RaceRoomListResponse,
  RoomMessagesResponse,
  PlaybackAction,
  ReplayAction,
  ReplayResponse,
  RoomDiagnostics,
} from "@/lib/types";

const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

async function request<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) {
    throw await responseError(response);
  }
  return response.json() as Promise<T>;
}

async function mutate<T>(path: string, body?: object): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) throw await responseError(response);
  return response.json() as Promise<T>;
}

async function responseError(response: Response): Promise<Error> {
  const fallback = `API request failed with HTTP ${response.status}`;
  try {
    const body = await response.json() as { detail?: string };
    return new Error(body.detail || fallback);
  } catch {
    return new Error(fallback);
  }
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

export function getRaceRooms(params: URLSearchParams, signal?: AbortSignal): Promise<RaceRoomListResponse> {
  return request<RaceRoomListResponse>(`/api/v1/race-rooms?${params}`, signal);
}

export function getRaceRoomEvents(params: URLSearchParams, signal?: AbortSignal): Promise<RaceRoomEventsResponse> {
  return request<RaceRoomEventsResponse>(`/api/v1/race-rooms/events?${params}`, signal);
}

export function getRaceRoom(slug: string, signal?: AbortSignal): Promise<RaceRoomDetailResponse> {
  return request<RaceRoomDetailResponse>(`/api/v1/race-rooms/${encodeURIComponent(slug)}`, signal);
}

export function getRoomMessages(slug: string, query = "", signal?: AbortSignal): Promise<RoomMessagesResponse> {
  return request<RoomMessagesResponse>(`/api/v1/race-rooms/${encodeURIComponent(slug)}/messages${query ? `?${query}` : ""}`, signal);
}

export function getMessageEvidence(slug: string, id: string): Promise<MessageEvidenceResponse> {
  return request<MessageEvidenceResponse>(`/api/v1/race-rooms/${encodeURIComponent(slug)}/messages/${id}/evidence`);
}

export function updateRoomPlayback(slug: string, body: PlaybackAction): Promise<ReplayResponse> {
  return mutate(`/api/v1/race-rooms/${encodeURIComponent(slug)}/playback`, body);
}

export function startRoomReplay(slug: string, action: ReplayAction): Promise<ReplayResponse> {
  return mutate(`/api/v1/race-rooms/${encodeURIComponent(slug)}/replay`, { action });
}

export function getRoomDiagnostics(slug: string, signal?: AbortSignal): Promise<RoomDiagnostics> {
  return request<RoomDiagnostics>(`/api/v1/race-rooms/${encodeURIComponent(slug)}/diagnostics`, signal);
}

export function roomStreamUrl(slug: string, afterSequence = 0): string {
  return `${API_URL}/api/v1/race-rooms/${encodeURIComponent(slug)}/stream?after_sequence=${afterSequence}`;
}

export { API_URL };
