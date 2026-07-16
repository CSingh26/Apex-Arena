// SPDX-License-Identifier: AGPL-3.0-only
import { describe, expect, it } from "vitest";

import { mergeRoomMessages } from "./room-state";
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
});
