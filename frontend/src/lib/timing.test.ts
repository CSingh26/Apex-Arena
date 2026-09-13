// SPDX-License-Identifier: AGPL-3.0-only
import { describe, expect, it } from "vitest";

import { formatGap, formatLapTime, formatPitStop, validDuration } from "./timing";

describe("timing formatting", () => {
  it("formats a conventional lap time and preserves millisecond precision", () => {
    expect(formatLapTime(61.234)).toBe("1:01.234");
    expect(formatLapTime(9.008)).toBe("9.008");
  });

  it("rejects missing, impossible, and non-finite values", () => {
    expect(validDuration(0)).toBe(false);
    expect(validDuration(Number.POSITIVE_INFINITY)).toBe(false);
    expect(formatLapTime(301)).toBe("—");
    expect(formatGap("  ")).toBe("—");
  });

  it("formats race gaps and pit-stop durations without leaking raw values", () => {
    expect(formatGap(1.2)).toBe("+1.200");
    expect(formatGap("LAPPED")).toBe("LAPPED");
    expect(formatPitStop(2.345)).toBe("2.35s");
    expect(formatPitStop(121)).toBe("—");
  });
});
