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

  it("prefixes browser-native URLs exactly once beneath the public mount", async () => {
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

  it("lets an explicit api base path override the derived one", async () => {
    vi.resetModules();
    vi.stubEnv("NEXT_PUBLIC_APP_BASE_PATH", "/apex-arena");
    vi.stubEnv("NEXT_PUBLIC_API_BASE_PATH", "/apex-arena/api");
    const { apiPath } = await import("@/lib/app-paths");

    expect(apiPath("/rooms")).toBe("/apex-arena/api/rooms");
  });

  it("builds public URLs on the public domain, never an internal origin", async () => {
    vi.resetModules();
    vi.stubEnv("NEXT_PUBLIC_APP_BASE_PATH", "/apex-arena");
    vi.stubEnv("NEXT_PUBLIC_APP_URL", "https://chaitanyasingh.org/apex-arena");
    const { publicUrl } = await import("@/lib/app-paths");

    // The configured URL already carries the base path; it must not double up.
    expect(publicUrl("/rooms")).toBe("https://chaitanyasingh.org/apex-arena/rooms");
    expect(publicUrl("/")).toBe("https://chaitanyasingh.org/apex-arena");
  });

  it("falls back to a relative public URL when no origin is configured", async () => {
    vi.resetModules();
    vi.stubEnv("NEXT_PUBLIC_APP_BASE_PATH", "/apex-arena");
    vi.stubEnv("NEXT_PUBLIC_APP_URL", "");
    const { publicUrl } = await import("@/lib/app-paths");

    expect(publicUrl("/rooms")).toBe("/apex-arena/rooms");
  });
});
