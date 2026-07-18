// SPDX-License-Identifier: AGPL-3.0-only
import type { RoomMessage } from "./types";

export const MAX_CACHED_ROOM_MESSAGES = 600;

export function mergeRoomMessages(
  current: RoomMessage[],
  incoming: RoomMessage[],
  limit = MAX_CACHED_ROOM_MESSAGES,
): RoomMessage[] {
  const indexed = new Map<string, RoomMessage>();
  const sequenceIndex = new Map<number, string>();
  const upsert = (message: RoomMessage) => {
    const previous = indexed.get(message.id);
    if (previous && previous.sequence !== message.sequence && sequenceIndex.get(previous.sequence) === message.id) {
      sequenceIndex.delete(previous.sequence);
    }
    const existingId = sequenceIndex.get(message.sequence);
    if (existingId && existingId !== message.id) indexed.delete(existingId);
    indexed.set(message.id, message);
    sequenceIndex.set(message.sequence, message.id);
  };
  [...current]
    .sort((left, right) => left.sequence - right.sequence || left.id.localeCompare(right.id))
    .forEach(upsert);
  incoming.forEach(upsert);
  return [...indexed.values()]
    .sort((left, right) => left.sequence - right.sequence)
    .slice(-limit);
}

export function roomMessageTime(message: RoomMessage): string {
  if (message.session_time != null) {
    const minutes = Math.floor(message.session_time / 60);
    const seconds = Math.floor(message.session_time % 60);
    return `${minutes}:${seconds.toString().padStart(2, "0")}`;
  }
  if (message.wall_time) {
    return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" })
      .format(new Date(message.wall_time));
  }
  return `#${message.sequence}`;
}
