// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Presentation-neutral readers for retained car telemetry.
 *
 * The contract's central honesty rule drives this whole file: a channel a
 * driver never published is *missing*, not zero. Nothing here substitutes a
 * default for an absent reading, and nothing bridges a gap in a series — a
 * line drawn across missing samples asserts values that were never observed.
 */
import type {
  DriverTelemetrySeries,
  TelemetryChannel,
  TelemetrySample,
  TelemetryWindow,
} from "@/lib/types";

/** Channel order as presented, most useful first. */
export const TELEMETRY_CHANNELS: readonly TelemetryChannel[] = [
  "speed",
  "rpm",
  "throttle",
  "brake",
  "gear",
  "drs",
];

export const CHANNEL_LABELS: Record<TelemetryChannel, string> = {
  speed: "Speed",
  rpm: "Engine RPM",
  throttle: "Throttle",
  brake: "Brake",
  gear: "Gear",
  drs: "DRS flag",
};

/**
 * Distinct copy per reason code. A reader must be able to tell "this session
 * kept no telemetry" from "the scan ceiling cut the window short".
 */
const REASON_COPY: Record<string, string> = {
  no_telemetry_retained:
    "No car telemetry was retained for this session, so there is nothing to compare.",
  no_telemetry_for_lap:
    "No car telemetry was retained for the selected lap. Another lap may still have coverage.",
  telemetry_missing_for_some_drivers:
    "Telemetry came back for some of the selected cars only. The cars without a trace are named "
    + "below rather than drawn as empty lines.",
  scan_limit_reached:
    "The read hit its scan ceiling before the full window was covered, so these traces are a "
    + "partial view of the selection, not the whole of it.",
};

export function telemetryReasonCopy(reason: string | null | undefined): string | null {
  if (!reason) return null;
  return REASON_COPY[reason]
    ?? `The backend reported "${reason}" for this read, which this view does not have specific copy for.`;
}

export function channelUnit(window: TelemetryWindow, channel: TelemetryChannel): string {
  return window.units?.[channel] ?? "";
}

export function isTelemetryChannel(value: string): value is TelemetryChannel {
  return (TELEMETRY_CHANNELS as readonly string[]).includes(value);
}

/** `channels` is authoritative: never infer availability from the samples. */
export function driverHasChannel(
  driver: DriverTelemetrySeries,
  channel: TelemetryChannel,
): boolean {
  return driver.channels.includes(channel);
}

/** Channels at least one selected driver actually published, in display order. */
export function availableChannels(window: TelemetryWindow): TelemetryChannel[] {
  return TELEMETRY_CHANNELS.filter((channel) =>
    window.drivers.some((driver) => driverHasChannel(driver, channel)),
  );
}

/** Drivers the backend returned with no usable samples at all. */
export function driversWithoutSamples(window: TelemetryWindow): number[] {
  return window.drivers.filter((driver) => !driver.samples.length).map((driver) => driver.driver_number);
}

/** Drivers that have samples but did not publish this particular channel. */
export function driversMissingChannel(
  window: TelemetryWindow,
  channel: TelemetryChannel,
): number[] {
  return window.drivers
    .filter((driver) => driver.samples.length > 0 && !driverHasChannel(driver, channel))
    .map((driver) => driver.driver_number);
}

export type SeriesPoint = { x: number; y: number };

export type ChannelSeries = {
  driverNumber: number;
  /**
   * Contiguous runs of observed values. A new segment starts wherever a sample
   * had no value for this channel, so a gap is drawn as a gap.
   */
  segments: SeriesPoint[][];
  minimum: number;
  maximum: number;
  /** Chronologically last observed value, or `null` when none was observed. */
  last: number | null;
  observations: number;
};

function channelValue(sample: TelemetrySample, channel: TelemetryChannel): number | null {
  const raw = sample[channel];
  if (raw == null) return null;
  if (typeof raw === "boolean") return raw ? 1 : 0;
  return Number.isFinite(raw) ? raw : null;
}

function elapsedSeconds(sample: TelemetrySample, origin: number): number | null {
  const observed = Date.parse(sample.observed_at);
  if (!Number.isFinite(observed)) return null;
  return (observed - origin) / 1000;
}

/**
 * One driver's series for one channel, on an x axis of seconds elapsed from
 * that driver's own first sample in the window. Two cars set the same lap at
 * different wall-clock times, so a shared absolute clock would not line up.
 */
export function channelSeries(
  driver: DriverTelemetrySeries,
  channel: TelemetryChannel,
): ChannelSeries | null {
  if (!driverHasChannel(driver, channel) || !driver.samples.length) return null;
  const ordered = [...driver.samples].sort((left, right) => left.sequence - right.sequence);
  const origin = Date.parse(ordered[0].observed_at);
  const usableClock = Number.isFinite(origin);

  const segments: SeriesPoint[][] = [];
  let current: SeriesPoint[] = [];
  let minimum = Number.POSITIVE_INFINITY;
  let maximum = Number.NEGATIVE_INFINITY;
  let last: number | null = null;
  let observations = 0;

  ordered.forEach((sample, index) => {
    const y = channelValue(sample, channel);
    if (y == null) {
      // A missing reading ends the run. It is never interpolated across.
      if (current.length) segments.push(current);
      current = [];
      return;
    }
    const x = usableClock ? elapsedSeconds(sample, origin) : index;
    if (x == null) {
      if (current.length) segments.push(current);
      current = [];
      return;
    }
    current.push({ x, y });
    minimum = Math.min(minimum, y);
    maximum = Math.max(maximum, y);
    last = y;
    observations += 1;
  });
  if (current.length) segments.push(current);

  if (!observations) return null;
  return { driverNumber: driver.driver_number, segments, minimum, maximum, last, observations };
}

export type ChannelDomain = { minX: number; maxX: number; minY: number; maxY: number };

/**
 * The drawing domain across every series for a channel.
 *
 * A flat series is padded symmetrically so it renders as a level line rather
 * than collapsing onto an axis, which would read as a value of zero.
 */
export function channelDomain(series: readonly ChannelSeries[]): ChannelDomain | null {
  const points = series.flatMap((entry) => entry.segments.flat());
  if (!points.length) return null;
  const xs = points.map((point) => point.x);
  const ys = points.map((point) => point.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const padding = maxY === minY ? Math.max(Math.abs(maxY) * 0.05, 0.5) : 0;
  return {
    minX,
    maxX: maxX === minX ? minX + 1 : maxX,
    minY: minY - padding,
    maxY: maxY + padding,
  };
}

/** An SVG path for one segment, in the given pixel box. */
export function segmentPath(
  segment: readonly SeriesPoint[],
  domain: ChannelDomain,
  width: number,
  height: number,
): string {
  const spanX = domain.maxX - domain.minX || 1;
  const spanY = domain.maxY - domain.minY || 1;
  return segment
    .map((point, index) => {
      const x = ((point.x - domain.minX) / spanX) * width;
      const y = height - ((point.y - domain.minY) / spanY) * height;
      return `${index === 0 ? "M" : "L"}${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
}

/** Values formatted the way the channel is actually read, never as raw floats. */
export function formatChannelValue(
  value: number | null,
  channel: TelemetryChannel,
  unit: string,
): string {
  if (value == null || !Number.isFinite(value)) return "—";
  if (channel === "drs") return value >= 0.5 ? "Open" : "Closed";
  if (channel === "gear") return value === 0 ? "N" : String(Math.round(value));
  const rounded = channel === "rpm" || channel === "speed"
    ? Math.round(value).toLocaleString("en-GB")
    : Math.round(value * 10) / 10;
  return unit ? `${rounded} ${unit}` : String(rounded);
}

export function truncatedDrivers(window: TelemetryWindow): number[] {
  return window.drivers.filter((driver) => driver.samples_truncated).map((driver) => driver.driver_number);
}
