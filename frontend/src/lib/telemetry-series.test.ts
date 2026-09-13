// SPDX-License-Identifier: AGPL-3.0-only
import { describe, expect, it } from "vitest";

import {
  availableChannels,
  channelDomain,
  channelSeries,
  channelUnit,
  driverHasChannel,
  driversMissingChannel,
  driversWithoutSamples,
  formatChannelValue,
  isTelemetryChannel,
  segmentPath,
  telemetryReasonCopy,
  truncatedDrivers,
} from "@/lib/telemetry-series";
import { telemetrySample, telemetryWindow } from "@/test/strategy-fixtures";
import type { DriverTelemetrySeries } from "@/lib/types";

function series(
  driverNumber: number,
  channels: string[],
  samples: DriverTelemetrySeries["samples"],
  truncated = false,
): DriverTelemetrySeries {
  return {
    driver_number: driverNumber,
    channels,
    samples,
    samples_truncated: truncated,
  };
}

/** Fails loudly instead of asserting away a genuinely nullable reader result. */
function present<T>(value: T | null, what: string): T {
  if (value === null) throw new Error(`expected a ${what}`);
  return value;
}

const CLOCK = (offsetSeconds: number) =>
  new Date(Date.parse("2026-08-10T13:19:30Z") + offsetSeconds * 1000).toISOString();

describe("telemetryReasonCopy", () => {
  it("gives every contract reason its own explanation", () => {
    const copies = [
      "no_telemetry_retained",
      "no_telemetry_for_lap",
      "telemetry_missing_for_some_drivers",
      "scan_limit_reached",
    ].map((reason) => telemetryReasonCopy(reason));

    expect(new Set(copies).size).toBe(4);
    expect(copies.every((copy) => typeof copy === "string" && copy.length > 20)).toBe(true);
    expect(telemetryReasonCopy("no_telemetry_for_lap")).toContain("selected lap");
    expect(telemetryReasonCopy("scan_limit_reached")).toContain("scan ceiling");
  });

  it("says an unknown reason is unknown rather than inventing one", () => {
    expect(telemetryReasonCopy("brand_new_reason")).toContain("brand_new_reason");
    expect(telemetryReasonCopy(null)).toBeNull();
  });
});

describe("channel availability", () => {
  it("treats the published channel list as authoritative", () => {
    const driver = series(4, ["speed"], [telemetrySample({ brake: 55 })]);
    expect(driverHasChannel(driver, "speed")).toBe(true);
    // The sample carries a brake value, but the driver did not publish the
    // channel, so it is not offered.
    expect(driverHasChannel(driver, "brake")).toBe(false);
  });

  it("offers a channel when at least one selected driver published it", () => {
    const window = telemetryWindow({
      drivers: [
        series(4, ["speed", "rpm"], [telemetrySample()]),
        series(16, ["speed"], [telemetrySample()]),
      ],
    });

    expect(availableChannels(window)).toEqual(["speed", "rpm"]);
    expect(driversMissingChannel(window, "rpm")).toEqual([16]);
    expect(driversMissingChannel(window, "speed")).toEqual([]);
  });

  it("does not blame a channel on a driver with no samples at all", () => {
    const window = telemetryWindow({
      drivers: [series(4, ["speed"], [telemetrySample()]), series(16, [], [])],
    });

    expect(driversWithoutSamples(window)).toEqual([16]);
    expect(driversMissingChannel(window, "speed")).toEqual([]);
  });

  it("reads units from the published map", () => {
    const window = telemetryWindow();
    expect(channelUnit(window, "rpm")).toBe("rpm");
    expect(channelUnit(window, "speed")).toBe("km/h");
    expect(channelUnit(window, "gear")).toBe("");
  });

  it("recognises only contract channels", () => {
    expect(isTelemetryChannel("rpm")).toBe(true);
    expect(isTelemetryChannel("tyre_pressure")).toBe(false);
  });
});

describe("channelSeries", () => {
  it("places points on seconds elapsed from the driver's own first sample", () => {
    const result = channelSeries(
      series(4, ["speed"], [
        telemetrySample({ sequence: 1, observed_at: CLOCK(0), speed: 280 }),
        telemetrySample({ sequence: 2, observed_at: CLOCK(2), speed: 300 }),
      ]),
      "speed",
    );

    expect(result?.segments).toEqual([[{ x: 0, y: 280 }, { x: 2, y: 300 }]]);
    expect(result?.minimum).toBe(280);
    expect(result?.maximum).toBe(300);
    expect(result?.last).toBe(300);
    expect(result?.observations).toBe(2);
  });

  it("breaks the line at a missing reading instead of drawing through it", () => {
    const result = channelSeries(
      series(4, ["brake"], [
        telemetrySample({ sequence: 1, observed_at: CLOCK(0), brake: 10 }),
        telemetrySample({ sequence: 2, observed_at: CLOCK(1), brake: null }),
        telemetrySample({ sequence: 3, observed_at: CLOCK(2), brake: 80 }),
      ]),
      "brake",
    );

    expect(result?.segments).toHaveLength(2);
    expect(result?.segments.flat().map((point) => point.y)).toEqual([10, 80]);
  });

  it("never substitutes zero for a channel the driver did not publish", () => {
    const driver = series(4, ["speed"], [telemetrySample({ brake: null })]);
    expect(channelSeries(driver, "brake")).toBeNull();
  });

  it("returns nothing when every reading for a published channel is absent", () => {
    const driver = series(4, ["brake"], [telemetrySample({ brake: null })]);
    expect(channelSeries(driver, "brake")).toBeNull();
  });

  it("reads the boolean DRS flag as an observed two-state channel", () => {
    const result = channelSeries(
      series(4, ["drs"], [
        telemetrySample({ sequence: 1, observed_at: CLOCK(0), drs: false }),
        telemetrySample({ sequence: 2, observed_at: CLOCK(1), drs: true }),
      ]),
      "drs",
    );

    expect(result?.segments.flat().map((point) => point.y)).toEqual([0, 1]);
  });

  it("orders by published sequence rather than arrival order", () => {
    const result = channelSeries(
      series(4, ["speed"], [
        telemetrySample({ sequence: 3, observed_at: CLOCK(2), speed: 310 }),
        telemetrySample({ sequence: 1, observed_at: CLOCK(0), speed: 290 }),
      ]),
      "speed",
    );

    expect(result?.segments.flat().map((point) => point.y)).toEqual([290, 310]);
  });
});

describe("channelDomain", () => {
  it("spans every series drawn on the chart", () => {
    const first = channelSeries(
      series(4, ["speed"], [
        telemetrySample({ sequence: 1, observed_at: CLOCK(0), speed: 280 }),
        telemetrySample({ sequence: 2, observed_at: CLOCK(4), speed: 300 }),
      ]),
      "speed",
    );
    const second = channelSeries(
      series(16, ["speed"], [telemetrySample({ sequence: 1, observed_at: CLOCK(0), speed: 320 })]),
      "speed",
    );

    expect(channelDomain([present(first, "series"), present(second, "series")])).toEqual({ minX: 0, maxX: 4, minY: 280, maxY: 320 });
  });

  it("pads a flat series so it renders level rather than on the axis", () => {
    const flat = channelSeries(
      series(4, ["gear"], [
        telemetrySample({ sequence: 1, observed_at: CLOCK(0), gear: 7 }),
        telemetrySample({ sequence: 2, observed_at: CLOCK(1), gear: 7 }),
      ]),
      "gear",
    );
    const domain = channelDomain([present(flat, "series")]);

    expect(domain?.minY).toBeLessThan(7);
    expect(domain?.maxY).toBeGreaterThan(7);
  });

  it("has no domain when nothing was observed", () => {
    expect(channelDomain([])).toBeNull();
  });
});

describe("segmentPath", () => {
  it("maps the domain onto the pixel box with the y axis pointing up", () => {
    const path = segmentPath(
      [{ x: 0, y: 0 }, { x: 10, y: 100 }],
      { minX: 0, maxX: 10, minY: 0, maxY: 100 },
      100,
      50,
    );

    expect(path).toBe("M0.00 50.00 L100.00 0.00");
  });
});

describe("formatChannelValue", () => {
  it("formats each channel the way it is actually read", () => {
    expect(formatChannelValue(11_400, "rpm", "rpm")).toBe("11,400 rpm");
    expect(formatChannelValue(302.4, "speed", "km/h")).toBe("302 km/h");
    expect(formatChannelValue(96.44, "throttle", "%")).toBe("96.4 %");
    expect(formatChannelValue(7, "gear", "")).toBe("7");
    expect(formatChannelValue(0, "gear", "")).toBe("N");
    expect(formatChannelValue(1, "drs", "")).toBe("Open");
    expect(formatChannelValue(0, "drs", "")).toBe("Closed");
  });

  it("shows an em dash rather than a zero for an absent value", () => {
    expect(formatChannelValue(null, "brake", "%")).toBe("—");
    expect(formatChannelValue(Number.NaN, "brake", "%")).toBe("—");
  });
});

describe("truncatedDrivers", () => {
  it("names only the drivers whose series was cut short", () => {
    const window = telemetryWindow({
      drivers: [
        series(4, ["speed"], [telemetrySample()], true),
        series(16, ["speed"], [telemetrySample()], false),
      ],
    });

    expect(truncatedDrivers(window)).toEqual([4]);
  });
});
