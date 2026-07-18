// SPDX-License-Identifier: AGPL-3.0-only
import { afterEach, describe, expect, it, vi } from "vitest";

async function paths(basePath?: string) {
  vi.resetModules();
  if (basePath === undefined) vi.unstubAllEnvs();
  else vi.stubEnv("NEXT_PUBLIC_APP_BASE_PATH", basePath);
  return import("@/lib/app-paths");
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("application paths", () => {
  it("keeps local development at the origin root", async () => {
    const { apiPath, stripBasePath, withBasePath } = await paths("");

    expect(withBasePath("/rooms")).toBe("/rooms");
    expect(apiPath("rooms?season=2026")).toBe("/api/rooms?season=2026");
    expect(stripBasePath("/rooms/spa")).toBe("/rooms/spa");
  });

  it("prefixes browser-native URLs exactly once in Cloudflare builds", async () => {
    const { apiPath, publicAssetPath, stripBasePath, withBasePath } = await paths("/apex-arena/");

    expect(withBasePath("/rooms")).toBe("/apex-arena/rooms");
    expect(withBasePath("/apex-arena/rooms")).toBe("/apex-arena/rooms");
    expect(apiPath("/rooms/spa?after=4")).toBe("/apex-arena/api/rooms/spa?after=4");
    expect(publicAssetPath("circuits/spa.svg")).toBe("/apex-arena/circuits/spa.svg");
    expect(stripBasePath("/apex-arena/rooms/spa")).toBe("/rooms/spa");
    expect(stripBasePath("/apex-arena")).toBe("/");
  });

  it("leaves absolute external URLs unchanged", async () => {
    const { withBasePath } = await paths("apex-arena");

    expect(withBasePath("https://example.com/asset.svg")).toBe("https://example.com/asset.svg");
  });
});
