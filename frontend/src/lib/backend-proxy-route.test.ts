// SPDX-License-Identifier: AGPL-3.0-only
import type { NextRequest } from "next/server";
import { afterEach, expect, it, vi } from "vitest";
import { GET } from "@/app/api/[[...path]]/route";

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

it.each([429, 503])("preserves backend HTTP %s and Retry-After without buffering a fake success", async (status) => {
  vi.stubEnv("BACKEND_INTERNAL_URL", "http://synthetic-backend:8000");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
    JSON.stringify({ code: "rate_limit_exceeded", detail: "Retry shortly" }),
    { status, headers: { "Retry-After": "30", "Cache-Control": "no-store", "Content-Type": "application/json" } },
  )));
  const response = await GET(new Request("http://synthetic-frontend/api/rooms/race/stream") as NextRequest, {
    params: Promise.resolve({ path: ["rooms", "race", "stream"] }),
  });
  expect(response.status).toBe(status);
  expect(response.headers.get("retry-after")).toBe("30");
  expect(response.headers.get("cache-control")).toBe("no-store");
  expect(await response.json()).toHaveProperty("code", "rate_limit_exceeded");
});

it("propagates request abort to upstream fetch and cancels upstream body on downstream cancellation", async () => {
  vi.stubEnv("BACKEND_INTERNAL_URL", "http://synthetic-backend:8000");
  const cancel = vi.fn();
  const fetchMock = vi.fn().mockResolvedValue(new Response(new ReadableStream({ cancel }), {
    headers: { "Content-Type": "text/event-stream" },
  }));
  vi.stubGlobal("fetch", fetchMock);
  const controller = new AbortController();
  const request = new Request("http://synthetic-frontend/api/rooms/race/stream", { signal: controller.signal });
  const response = await GET(request as NextRequest, {
    params: Promise.resolve({ path: ["rooms", "race", "stream"] }),
  });
  const forwardedSignal = fetchMock.mock.calls[0][1].signal as AbortSignal;
  expect(forwardedSignal).toBe(request.signal);
  controller.abort();
  expect(forwardedSignal.aborted).toBe(true);
  await response.body!.cancel();
  expect(cancel).toHaveBeenCalledOnce();
});
