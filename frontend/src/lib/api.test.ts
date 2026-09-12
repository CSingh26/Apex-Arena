// SPDX-License-Identifier: AGPL-3.0-only
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  startRoomReplay,
  updateRoomPlayback,
  verifyReplayOperator,
} from "@/lib/api";

describe("replay operator client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sends the operator credential only as a custom header on replay mutations", async () => {
    const canary = "operator-canary-never-leak";
    const fetchMock = vi.fn().mockImplementation(async () => new Response(
      JSON.stringify({ authorized: true }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ));
    vi.stubGlobal("fetch", fetchMock);

    await verifyReplayOperator(canary);
    await startRoomReplay("spa-race", "start", canary);
    await updateRoomPlayback("spa-race", { action: "pause" }, canary);

    for (const [url, options] of fetchMock.mock.calls as [string, RequestInit][]) {
      expect(url).not.toContain(canary);
      expect(String(options.body ?? "")).not.toContain(canary);
      expect(new Headers(options.headers).get("X-Apex-Replay-Password")).toBe(
        Buffer.from(canary, "utf-8").toString("base64"),
      );
    }
    expect(fetchMock.mock.calls[0][1].body).toBeUndefined();
  });

  it.each([
    "plain-ascii-password",
    "opérateur-password",
    "operator-🔒-password",
    "  exact padded password  ",
  ])("encodes the exact UTF-8 password into an ASCII-safe Headers value: %s", async (password) => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ authorized: true }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ));
    vi.stubGlobal("fetch", fetchMock);

    await verifyReplayOperator(password);

    const headers = new Headers(fetchMock.mock.calls[0][1].headers);
    const wireValue = headers.get("X-Apex-Replay-Password");
    expect(wireValue).toBe(Buffer.from(password, "utf-8").toString("base64"));
    expect(wireValue).toMatch(/^[A-Za-z0-9+/]+={0,2}$/);
    expect(wireValue).not.toContain(password);
  });

  it("does not attach operator credentials to public reads", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      room: {}, agents: [], playback: {}, circuit: {}, weather: {}, intelligence: {},
    }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    const { getRaceRoom } = await import("@/lib/api");
    await getRaceRoom("spa-race");

    expect(new Headers(fetchMock.mock.calls[0][1].headers).has("X-Apex-Replay-Password")).toBe(false);
  });

  it("returns an error with HTTP status without reflecting a rejected secret", async () => {
    const canary = "rejected-operator-canary";
    const wireCanary = Buffer.from(canary, "utf-8").toString("base64");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: "Invalid replay operator credential" }),
      { status: 401, headers: { "Content-Type": "application/json" } },
    )));

    await expect(verifyReplayOperator(canary)).rejects.toMatchObject({
      status: 401,
      message: "Invalid replay operator credential",
    });
    await expect(verifyReplayOperator(canary)).rejects.not.toHaveProperty("message", expect.stringContaining(canary));
    await expect(verifyReplayOperator(canary)).rejects.not.toHaveProperty("message", expect.stringContaining(wireCanary));
  });
});
