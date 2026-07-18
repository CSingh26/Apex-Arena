// SPDX-License-Identifier: AGPL-3.0-only
import { describe, expect, it } from "vitest";

import { MAX_CACHED_ROOM_MESSAGES, mergeRoomMessages } from "./room-state";
import type { RoomMessage } from "./types";

const message = (id: string, sequence: number, content = id) => ({ id, sequence, content }) as RoomMessage;

describe("mergeRoomMessages", () => {
  it("deduplicates reconnect deliveries and restores sequence order", () => {
    expect(mergeRoomMessages([message("b", 2)], [message("a", 1), message("b", 2, "updated")]))
      .toEqual([message("a", 1), message("b", 2, "updated")]);
  });

  it("places a late out-of-order event correctly", () => {
    expect(mergeRoomMessages([message("a", 1), message("c", 3)], [message("b", 2)]).map((item) => item.sequence)).toEqual([1, 2, 3]);
  });

  it("deduplicates a reused sequence and prefers the incoming canonical record", () => {
    const result = mergeRoomMessages([message("stale", 4)], [message("canonical", 4, "replacement")]);
    expect(result).toEqual([message("canonical", 4, "replacement")]);
  });

  it("bounds local replay state while keeping the latest sequence window", () => {
    const incoming = Array.from({ length: MAX_CACHED_ROOM_MESSAGES + 25 }, (_, index) => message(String(index), index + 1));
    const merged = mergeRoomMessages([], incoming);
    expect(merged).toHaveLength(MAX_CACHED_ROOM_MESSAGES);
    expect(merged[0].sequence).toBe(26);
    expect(merged.at(-1)?.sequence).toBe(MAX_CACHED_ROOM_MESSAGES + 25);
  });
});
