// SPDX-License-Identifier: AGPL-3.0-only
import type { RoomMessage } from "./types";

export function mergeRoomMessages(current: RoomMessage[], incoming: RoomMessage[]): RoomMessage[] {
  const indexed = new Map(current.map((message) => [message.id, message]));
  incoming.forEach((message) => indexed.set(message.id, message));
  return [...indexed.values()].sort((left, right) => left.sequence - right.sequence);
}
