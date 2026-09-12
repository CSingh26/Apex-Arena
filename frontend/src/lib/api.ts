// SPDX-License-Identifier: AGPL-3.0-only
import type {
  EngineStatus,
  HealthResponse,
  SeasonCalendarSummary,
  SessionEventsResponse,
  SessionStateResponse,
  SessionTimingResponse,
  SessionTelemetryResponse,
  SessionLocationsResponse,
  SessionLocationSamplesResponse,
  SessionTrackResponse,
  MessageEvidenceResponse,
  RaceRoomDetailResponse,
  RaceRoomEventsResponse,
  RaceRoomListResponse,
  RoomMessagesResponse,
  PlaybackAction,
  ReplayAction,
  ReplayResponse,
  ReplayOperatorVerification,
  RoomDiagnostics,
  DriverStandingsResponse,
  ConstructorStandingsResponse,
  SessionBootstrap,
  SessionCapabilities,
  EventImportance,
  EventOrigin,
  RaceEventCategory,
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

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly retryAfterSeconds?: number) {
    super(message);
    this.name = "ApiError";
  }
}

function replayOperatorWireValue(password: string): string {
  const binary = Array.from(
    new TextEncoder().encode(password),
    (byte) => String.fromCharCode(byte),
  ).join("");
  return btoa(binary);
}

async function mutate<T>(path: string, body?: object, operatorPassword?: string): Promise<T> {
  const headers = new Headers({ Accept: "application/json" });
  if (body) headers.set("Content-Type", "application/json");
  if (operatorPassword) {
    headers.set("X-Apex-Replay-Password", replayOperatorWireValue(operatorPassword));
  }
  const response = await fetch(apiPath(path), {
    method: "POST",
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) throw await responseError(response);
  return response.json() as Promise<T>;
}

async function responseError(response: Response): Promise<Error> {
  const fallback = `API request failed with HTTP ${response.status}`;
  const retryAfterSeconds = retryAfter(response);
  const guidance = retryAfterSeconds ? ` Retry in ${retryAfterSeconds} seconds.` : "";
  try {
    const body = await response.json() as { detail?: string };
    // This existing server contract requires operator configuration, not time.
    // Never attach a synthesized retry deadline to a permanent disabled state.
    if (response.status === 503 && body.detail === "Replay operator access is not configured") {
      return new ApiError(body.detail, response.status);
    }
    return new ApiError(
      (typeof body.detail === "string" && body.detail ? body.detail : fallback) + guidance,
      response.status, retryAfterSeconds,
    );
  } catch {
    return new ApiError(fallback + guidance, response.status, retryAfterSeconds);
  }
}

function retryAfter(response: Response): number | undefined {
  if (response.status !== 429 && response.status !== 503) return undefined;
  const raw = response.headers.get("Retry-After")?.trim() ?? "";
  // Parse only delta-seconds or an HTTP date, not JS numeric conveniences.
  const seconds = /^\d+$/.test(raw)
    ? Number(raw)
    : /^[A-Za-z]{3}, /.test(raw) ? (Date.parse(raw) - Date.now()) / 1000 : NaN;
  return Number.isFinite(seconds) && seconds > 0 ? Math.min(300, Math.ceil(seconds)) : 5;
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

export function getDriverStandings(signal?: AbortSignal): Promise<DriverStandingsResponse> {
  return request<DriverStandingsResponse>("/championship/drivers", signal);
}

export function getConstructorStandings(signal?: AbortSignal): Promise<ConstructorStandingsResponse> {
  return request<ConstructorStandingsResponse>("/championship/constructors", signal);
}

export function getSessionEvents(
  sessionKey: string,
  options: {
    afterSequenceNumber?: number;
    beforeSequenceNumber?: number;
    limit?: number;
    eventTypes?: string[];
    category?: RaceEventCategory;
    driverNumber?: number;
    lapNumber?: number;
    minimumImportance?: EventImportance;
    eventOrigin?: EventOrigin;
    beforeTime?: string;
  } = {},
  signal?: AbortSignal,
): Promise<SessionEventsResponse> {
  const params = new URLSearchParams();
  if (options.afterSequenceNumber != null) {
    params.set("after_sequence_number", String(options.afterSequenceNumber));
  }
  if (options.beforeSequenceNumber != null) {
    params.set("before_sequence_number", String(options.beforeSequenceNumber));
  }
  if (options.limit != null) params.set("limit", String(options.limit));
  for (const eventType of options.eventTypes ?? []) params.append("event_type", eventType);
  if (options.category) params.set("category", options.category);
  if (options.driverNumber != null) params.set("driver_number", String(options.driverNumber));
  if (options.lapNumber != null) params.set("lap_number", String(options.lapNumber));
  if (options.minimumImportance) params.set("minimum_importance", options.minimumImportance);
  if (options.eventOrigin) params.set("event_origin", options.eventOrigin);
  if (options.beforeTime) params.set("before_time", options.beforeTime);
  const query = params.toString();
  return request<SessionEventsResponse>(
    `/sessions/${encodeURIComponent(sessionKey)}/events${query ? `?${query}` : ""}`,
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

export function getSessionTiming(sessionKey: string, signal?: AbortSignal): Promise<SessionTimingResponse> {
  return request<SessionTimingResponse>(`/sessions/${encodeURIComponent(sessionKey)}/timing`, signal);
}

export function getSessionTelemetry(
  sessionKey: string,
  driverNumber: number,
  signal?: AbortSignal,
): Promise<SessionTelemetryResponse> {
  return request<SessionTelemetryResponse>(
    `/sessions/${encodeURIComponent(sessionKey)}/drivers/${driverNumber}/telemetry`,
    signal,
  );
}

export function getSessionLocations(
  sessionKey: string,
  options: { at?: string } = {},
  signal?: AbortSignal,
): Promise<SessionLocationsResponse> {
  const query = options.at ? `?at=${encodeURIComponent(options.at)}` : "";
  return request<SessionLocationsResponse>(
    `/sessions/${encodeURIComponent(sessionKey)}/locations${query}`,
    signal,
  );
}

export function getSessionLocationSamples(
  sessionKey: string,
  options: { since?: string; until?: string; driverNumber?: number; limit?: number } = {},
  signal?: AbortSignal,
): Promise<SessionLocationSamplesResponse> {
  const params = new URLSearchParams();
  if (options.since) params.set("since", options.since);
  if (options.until) params.set("until", options.until);
  if (options.driverNumber != null) params.set("driver_number", String(options.driverNumber));
  if (options.limit != null) params.set("limit", String(options.limit));
  const query = params.toString();
  return request<SessionLocationSamplesResponse>(
    `/sessions/${encodeURIComponent(sessionKey)}/locations/samples${query ? `?${query}` : ""}`,
    signal,
  );
}

export function getSessionTrack(
  sessionKey: string,
  signal?: AbortSignal,
): Promise<SessionTrackResponse> {
  return request<SessionTrackResponse>(`/sessions/${encodeURIComponent(sessionKey)}/track`, signal);
}

export function getSession(sessionId: string, signal?: AbortSignal): Promise<SessionBootstrap> {
  return request<SessionBootstrap>(`/sessions/${encodeURIComponent(sessionId)}`, signal);
}

export function getSessionCapabilities(
  sessionId: string,
  signal?: AbortSignal,
): Promise<SessionCapabilities> {
  return request<SessionCapabilities>(`/sessions/${encodeURIComponent(sessionId)}/capabilities`, signal);
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

export function updateRoomPlayback(slug: string, body: PlaybackAction, operatorPassword: string): Promise<ReplayResponse> {
  return mutate(`/rooms/${encodeURIComponent(slug)}/playback`, body, operatorPassword);
}

export function startRoomReplay(slug: string, action: ReplayAction, operatorPassword: string): Promise<ReplayResponse> {
  return mutate(`/rooms/${encodeURIComponent(slug)}/replay`, { action }, operatorPassword);
}

export function verifyReplayOperator(operatorPassword: string): Promise<ReplayOperatorVerification> {
  return mutate<ReplayOperatorVerification>("/rooms/replay-operator/verify", undefined, operatorPassword);
}

export function getRoomDiagnostics(slug: string, signal?: AbortSignal): Promise<RoomDiagnostics> {
  return request<RoomDiagnostics>(`/rooms/${encodeURIComponent(slug)}/diagnostics`, signal);
}

export function roomStreamUrl(slug: string, afterSequence = 0, discussionGeneration?: number): string {
  const params = new URLSearchParams({ after_sequence: String(afterSequence) });
  if (discussionGeneration !== undefined) {
    params.set("discussion_generation", String(discussionGeneration));
  }
  return apiPath(`/rooms/${encodeURIComponent(slug)}/stream?${params.toString()}`);
}
