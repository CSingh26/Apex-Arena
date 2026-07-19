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
import { apiPath } from "@/lib/app-paths";

async function request<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(apiPath(path), {
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) {
    throw await responseError(response);
  }
  return response.json() as Promise<T>;
}

async function mutate<T>(path: string, body?: object): Promise<T> {
  const response = await fetch(apiPath(path), {
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
  return request<SeasonCalendarSummary>("/season/2026", signal);
}

export function getEngineStatus(signal?: AbortSignal): Promise<EngineStatus> {
  return request<EngineStatus>("/engine/status", signal);
}

export function getSessionEvents(
  sessionKey: string,
  signal?: AbortSignal,
): Promise<SessionEventsResponse> {
  return request<SessionEventsResponse>(
    `/sessions/${encodeURIComponent(sessionKey)}/events`,
    signal,
  );
}

export function getSessionState(
  sessionKey: string,
  signal?: AbortSignal,
): Promise<SessionStateResponse> {
  return request<SessionStateResponse>(
    `/sessions/${encodeURIComponent(sessionKey)}/state`,
    signal,
  );
}

export function sessionStreamUrl(sessionKey: string, lastSequenceNumber: number): string {
  return apiPath(`/stream/sessions/${encodeURIComponent(sessionKey)}?last_sequence_number=${lastSequenceNumber}`);
}

export function getRaceRooms(params: URLSearchParams, signal?: AbortSignal): Promise<RaceRoomListResponse> {
  return request<RaceRoomListResponse>(`/rooms?${params}`, signal);
}

export function getRaceRoomEvents(params: URLSearchParams, signal?: AbortSignal): Promise<RaceRoomEventsResponse> {
  return request<RaceRoomEventsResponse>(`/weekends?${params}`, signal);
}

export function getRaceRoom(slug: string, signal?: AbortSignal): Promise<RaceRoomDetailResponse> {
  return request<RaceRoomDetailResponse>(`/rooms/${encodeURIComponent(slug)}`, signal);
}

export function getRoomMessages(slug: string, query = "", signal?: AbortSignal): Promise<RoomMessagesResponse> {
  return request<RoomMessagesResponse>(`/rooms/${encodeURIComponent(slug)}/messages${query ? `?${query}` : ""}`, signal);
}

export function getMessageEvidence(slug: string, id: string): Promise<MessageEvidenceResponse> {
  return request<MessageEvidenceResponse>(`/rooms/${encodeURIComponent(slug)}/messages/${id}/evidence`);
}

export function updateRoomPlayback(slug: string, body: PlaybackAction): Promise<ReplayResponse> {
  return mutate(`/rooms/${encodeURIComponent(slug)}/playback`, body);
}

export function startRoomReplay(slug: string, action: ReplayAction): Promise<ReplayResponse> {
  return mutate(`/rooms/${encodeURIComponent(slug)}/replay`, { action });
}

export function getRoomDiagnostics(slug: string, signal?: AbortSignal): Promise<RoomDiagnostics> {
  return request<RoomDiagnostics>(`/rooms/${encodeURIComponent(slug)}/diagnostics`, signal);
}

export function roomStreamUrl(slug: string, afterSequence = 0): string {
  return apiPath(`/rooms/${encodeURIComponent(slug)}/stream?after_sequence=${afterSequence}`);
}
