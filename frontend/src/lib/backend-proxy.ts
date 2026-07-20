// SPDX-License-Identifier: AGPL-3.0-only

/** Translate the stable browser API contract to the existing FastAPI routes. */
export function backendPath(publicSegments: readonly string[]): string {
  const path = publicSegments.map((segment) => encodeURIComponent(segment)).join("/");
  if (path === "health") return "/health";
  if (path.startsWith("health/")) return `/${path}`;
  if (path === "weekends") return "/api/v1/race-rooms/events";
  if (path.startsWith("weekends/")) {
    return `/api/v1/race-rooms/events/${path.slice("weekends/".length)}`;
  }
  if (path === "rooms") return "/api/v1/race-rooms";
  if (path.startsWith("rooms/")) return `/api/v1/race-rooms/${path.slice("rooms/".length)}`;
  return `/api/v1/${path}`;
}
