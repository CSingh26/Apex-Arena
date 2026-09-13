// SPDX-License-Identifier: AGPL-3.0-only
import { afterEach, describe, expect, it, vi } from "vitest";
import { getHealth, verifyReplayOperator } from "@/lib/api";
import { reconnectDelay } from "@/lib/retry-delay";

afterEach(() => vi.unstubAllGlobals());

describe("rate-limited first-party requests", () => {
  it("does not promise a timed retry for permanently unconfigured operator access", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: "Replay operator access is not configured" }), { status: 503 },
    )));
    await expect(verifyReplayOperator("synthetic")).rejects.toMatchObject({
      status: 503,
      retryAfterSeconds: undefined,
      message: "Replay operator access is not configured",
    });
  });
  it.each([429, 503])("exposes bounded Retry-After and actionable HTTP %s without replaying mutations", async (status) => {
    const fetchMock = vi.fn().mockImplementation(async () => new Response(
      JSON.stringify({ detail: "Request admission is temporarily unavailable" }),
      { status, headers: { "Retry-After": "30" } },
    ));
    vi.stubGlobal("fetch", fetchMock);
    await expect(getHealth()).rejects.toMatchObject({ status, retryAfterSeconds: 30,
      message: expect.stringContaining("Retry in 30 seconds") });
    await expect(verifyReplayOperator("synthetic")).rejects.toMatchObject({ status, retryAfterSeconds: 30 });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each(["Infinity", "1e309", "-20", "0", "not-a-date"])("does not propagate malformed Retry-After %s", async (header) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("unavailable", {
      status: 503, headers: { "Retry-After": header },
    })));
    await expect(getHealth()).rejects.toMatchObject({ status: 503, retryAfterSeconds: 5 });
  });

  it("caps an excessive Retry-After and accepts a finite HTTP date", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response("", { status: 429, headers: { "Retry-After": "9999999" } }))
      .mockResolvedValueOnce(new Response("", { status: 503, headers: { "Retry-After": new Date(Date.now() + 30_000).toUTCString() } }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(getHealth()).rejects.toMatchObject({ retryAfterSeconds: 300 });
    await expect(getHealth()).rejects.toMatchObject({ retryAfterSeconds: expect.any(Number) });
  });

  it("backs opaque EventSource errors off beyond eight seconds with bounded jitter", () => {
    expect(reconnectDelay(1, 500, () => 1)).toBe(1000);
    expect(reconnectDelay(1, 750, () => 1)).toBe(1500);
    expect(reconnectDelay(8, 750, () => 1)).toBe(60_000);
    expect(reconnectDelay(8, 750, () => 0)).toBe(45_000);
    expect(reconnectDelay(Number.POSITIVE_INFINITY, 500, () => 1)).toBe(60_000);
  });
});
