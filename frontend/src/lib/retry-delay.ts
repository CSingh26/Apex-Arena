// SPDX-License-Identifier: AGPL-3.0-only
/** Native EventSource hides HTTP status/Retry-After; bounded jitter avoids a retry herd. */
export function reconnectDelay(attempt: number, baseMs: number, random = Math.random): number {
  const capped = Math.min(60_000, baseMs * 2 ** Math.min(10, Math.max(0, attempt)));
  return Math.round(capped * (0.75 + 0.25 * Math.max(0, Math.min(1, random()))));
}
