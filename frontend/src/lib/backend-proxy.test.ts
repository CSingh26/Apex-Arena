// SPDX-License-Identifier: AGPL-3.0-only
import { describe, expect, it } from "vitest";

import { backendPath } from "@/lib/backend-proxy";

describe("public API translation", () => {
  it.each([
    [["health"], "/health"],
    [["health", "ready"], "/health/ready"],
    [["weekends"], "/api/v1/race-rooms/events"],
    [["rooms"], "/api/v1/race-rooms"],
    [["rooms", "spa-race", "stream"], "/api/v1/race-rooms/spa-race/stream"],
    [["season", "2026"], "/api/v1/season/2026"],
  ])("maps %j to %s", (segments, expected) => {
    expect(backendPath(segments)).toBe(expected);
  });

  it("encodes untrusted route segments", () => {
    expect(backendPath(["rooms", "spa race"])).toBe("/api/v1/race-rooms/spa%20race");
  });
});
