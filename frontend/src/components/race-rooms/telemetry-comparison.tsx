// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from "react";

import { ApiError, getSessionTelemetryHistory } from "@/lib/api";
import {
  CHANNEL_LABELS,
  availableChannels,
  channelDomain,
  channelSeries,
  channelUnit,
  driversMissingChannel,
  driversWithoutSamples,
  formatChannelValue,
  segmentPath,
  telemetryReasonCopy,
  truncatedDrivers,
  type ChannelSeries,
} from "@/lib/telemetry-series";
import type { TelemetryChannel, TelemetryWindow } from "@/lib/types";

import styles from "./telemetry-comparison.module.css";

/** The backend refuses more than two cars per read, so the form refuses too. */
const MAX_DRIVERS = 2;
const PLOT_WIDTH = 600;
const PLOT_HEIGHT = 110;

export type TelemetryDriverOption = { driverNumber: number; name: string };

type TelemetryComparisonProps = {
  sessionKey: string;
  drivers: TelemetryDriverOption[];
  /** Offered as the default lap so the form opens on something meaningful. */
  currentLap: number | null;
  /**
   * The consumed cursor the room is rendering against. Read at request time
   * only: a moving cursor must never trigger a telemetry fetch of its own.
   */
  viewSequence: number | null;
};

type RequestState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; window: TelemetryWindow };

function driverLabel(drivers: TelemetryDriverOption[], driverNumber: number): string {
  return drivers.find((driver) => driver.driverNumber === driverNumber)?.name ?? `Car ${driverNumber}`;
}

function carList(drivers: TelemetryDriverOption[], numbers: number[]): string {
  return numbers.map((number) => driverLabel(drivers, number)).join(" and ");
}

function seriesSummary(
  series: ChannelSeries,
  channel: TelemetryChannel,
  unit: string,
  name: string,
): string {
  const low = formatChannelValue(series.minimum, channel, unit);
  const high = formatChannelValue(series.maximum, channel, unit);
  return `${name}: ${low} to ${high} across ${series.observations} readings`;
}

function ChannelChart({
  channel,
  unit,
  series,
  drivers,
  missing,
  silent,
  slotFor,
}: {
  channel: TelemetryChannel;
  unit: string;
  series: ChannelSeries[];
  drivers: TelemetryDriverOption[];
  missing: number[];
  /** Published the channel but observed no value inside this window. */
  silent: number[];
  /** Keeps a trace's colour tied to the legend, not to its order in `series`. */
  slotFor: (driverNumber: number) => number;
}) {
  const domain = channelDomain(series);
  const label = CHANNEL_LABELS[channel];

  return (
    <figure className={styles.chart}>
      <figcaption>
        <div className={styles.chartHeader}>
          <h4>{label}</h4>
          {unit ? <span className={styles.unit}>{unit}</span> : null}
        </div>
        <ul className={styles.ranges}>
          {series.map((entry) => (
            <li key={entry.driverNumber} data-slot={slotFor(entry.driverNumber)}>
              {seriesSummary(entry, channel, unit, driverLabel(drivers, entry.driverNumber))}
            </li>
          ))}
        </ul>
      </figcaption>
      {domain ? (
        <svg
          className={styles.plot}
          viewBox={`0 0 ${PLOT_WIDTH} ${PLOT_HEIGHT}`}
          preserveAspectRatio="none"
          role="img"
          aria-label={`${label} trace. ${series
            .map((entry) => seriesSummary(entry, channel, unit, driverLabel(drivers, entry.driverNumber)))
            .join(". ")}.`}
        >
          {[0.25, 0.5, 0.75].map((fraction) => (
            <line
              key={fraction}
              className={styles.grid}
              x1={0}
              x2={PLOT_WIDTH}
              y1={PLOT_HEIGHT * fraction}
              y2={PLOT_HEIGHT * fraction}
              vectorEffect="non-scaling-stroke"
            />
          ))}
          {series.map((entry) =>
            entry.segments.map((segment, segmentIndex) => (
              <path
                key={`${entry.driverNumber}-${segmentIndex}`}
                className={styles.trace}
                data-slot={slotFor(entry.driverNumber)}
                d={segmentPath(segment, domain, PLOT_WIDTH, PLOT_HEIGHT)}
                vectorEffect="non-scaling-stroke"
              />
            )),
          )}
        </svg>
      ) : null}
      {domain ? (
        <p className={styles.axis}>
          <span>{formatChannelValue(domain.maxY, channel, unit)} high</span>
          <span>{formatChannelValue(domain.minY, channel, unit)} low</span>
        </p>
      ) : null}
      {missing.length ? (
        <p className={styles.missing}>
          {carList(drivers, missing)} published no {label.toLowerCase()} channel for this window, so
          no trace is drawn. A missing channel is not a reading of zero.
        </p>
      ) : null}
      {silent.length ? (
        <p className={styles.missing}>
          {carList(drivers, silent)} published the {label.toLowerCase()} channel but no value landed
          inside this window, so there is nothing to draw.
        </p>
      ) : null}
    </figure>
  );
}

function WindowNotices({
  window: telemetry,
  drivers,
}: {
  window: TelemetryWindow;
  drivers: TelemetryDriverOption[];
}) {
  const notices: string[] = [];
  const reason = telemetryReasonCopy(telemetry.reason);
  if (telemetry.availability !== "available" && reason) notices.push(reason);
  const empty = driversWithoutSamples(telemetry);
  if (empty.length) {
    notices.push(`No telemetry came back for ${carList(drivers, empty)} in this window.`);
  }
  const truncated = truncatedDrivers(telemetry);
  if (truncated.length) {
    notices.push(
      `The series for ${carList(drivers, truncated)} hit the per-driver sample ceiling and was cut `
        + "short, so the trace ends before the window does.",
    );
  }
  if (telemetry.scan_limited && telemetry.reason !== "scan_limit_reached") {
    notices.push(
      "The backend stopped scanning before it exhausted the window, so coverage may be uneven.",
    );
  }
  if (!notices.length) return null;
  return (
    <ul className={styles.notices} aria-label="Telemetry coverage notices">
      {notices.map((notice) => (
        <li key={notice}>{notice}</li>
      ))}
    </ul>
  );
}

function TelemetryResult({
  window: telemetry,
  drivers,
}: {
  window: TelemetryWindow;
  drivers: TelemetryDriverOption[];
}) {
  const channels = availableChannels(telemetry);
  const slots = new Map(telemetry.drivers.map((driver, index) => [driver.driver_number, index]));
  const slotFor = (driverNumber: number) => slots.get(driverNumber) ?? 0;
  return (
    <div className={styles.result}>
      <p className={styles.status} data-availability={telemetry.availability}>
        {telemetry.availability === "available" ? "Telemetry available" : null}
        {telemetry.availability === "partial" ? "Telemetry partially available" : null}
        {telemetry.availability === "unavailable" ? "Telemetry unavailable" : null}
        {telemetry.lap_number == null ? " · latest retained window" : ` · lap ${telemetry.lap_number}`}
        {` · read at cursor ${telemetry.view_sequence}`}
      </p>
      <WindowNotices window={telemetry} drivers={drivers} />
      {channels.length ? (
        <>
          <ul className={styles.legend} aria-label="Trace colours">
            {telemetry.drivers.map((driver, index) => (
              <li key={driver.driver_number} data-slot={index}>
                <i aria-hidden />
                {driverLabel(drivers, driver.driver_number)}
              </li>
            ))}
          </ul>
          <div className={styles.charts}>
            {channels.map((channel) => {
              const series = telemetry.drivers
                .map((driver) => channelSeries(driver, channel))
                .filter((entry): entry is ChannelSeries => entry !== null);
              const drawn = new Set(series.map((entry) => entry.driverNumber));
              return (
                <ChannelChart
                  key={channel}
                  channel={channel}
                  unit={channelUnit(telemetry, channel)}
                  series={series}
                  drivers={drivers}
                  missing={driversMissingChannel(telemetry, channel)}
                  silent={telemetry.drivers
                    .filter(
                      (driver) =>
                        driver.channels.includes(channel) && !drawn.has(driver.driver_number),
                    )
                    .map((driver) => driver.driver_number)}
                  slotFor={slotFor}
                />
              );
            })}
          </div>
          <p className={styles.axisNote}>
            Each trace runs on seconds elapsed from that car&apos;s own first sample in this window.
            Gaps in a line are samples with no published reading and are left as gaps.
          </p>
        </>
      ) : (
        <p className={styles.empty}>
          No channel came back with a published value for this selection, so there is nothing to
          plot. Nothing has been substituted in its place.
        </p>
      )}
    </div>
  );
}

/**
 * Analyst-only telemetry comparison for up to two cars.
 *
 * The read is bounded and expensive, so it happens on an explicit submit and
 * never on a timing tick; a superseded request is aborted rather than allowed
 * to resolve over a newer one.
 */
export function TelemetryComparison({
  sessionKey,
  drivers,
  currentLap,
  viewSequence,
}: TelemetryComparisonProps) {
  const [selected, setSelected] = useState<number[]>([]);
  const [lapInput, setLapInput] = useState("");
  const [request, setRequest] = useState<RequestState>({ status: "idle" });
  const controllerRef = useRef<AbortController | null>(null);
  const cursorRef = useRef<number | null>(viewSequence);
  const formId = useId();

  // The cursor is read at request time only. Keeping it in a ref is what stops
  // a moving view sequence from turning into a fetch on every timing tick.
  useEffect(() => {
    cursorRef.current = viewSequence;
  }, [viewSequence]);

  // The caller keys this component by session, so a session change remounts it
  // and the unmount cleanup below aborts whatever read was still in flight.
  useEffect(() => () => controllerRef.current?.abort(), []);

  const toggleDriver = useCallback((driverNumber: number) => {
    setSelected((current) => {
      if (current.includes(driverNumber)) {
        return current.filter((number) => number !== driverNumber);
      }
      if (current.length >= MAX_DRIVERS) return current;
      return [...current, driverNumber];
    });
  }, []);

  const compare = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (!selected.length) return;
      controllerRef.current?.abort();
      const controller = new AbortController();
      controllerRef.current = controller;
      setRequest({ status: "loading" });
      const parsedLap = Number.parseInt(lapInput, 10);
      try {
        const telemetry = await getSessionTelemetryHistory(
          sessionKey,
          selected,
          {
            lapNumber: Number.isFinite(parsedLap) && parsedLap >= 0 ? parsedLap : null,
            cursor: cursorRef.current,
          },
          controller.signal,
        );
        if (controller.signal.aborted) return;
        setRequest({ status: "ready", window: telemetry });
      } catch (error) {
        if (controller.signal.aborted) return;
        const message = error instanceof ApiError
          ? error.message
          : "Telemetry could not be read for this selection.";
        setRequest({ status: "error", message });
      }
    },
    [lapInput, selected, sessionKey],
  );

  const atLimit = selected.length >= MAX_DRIVERS;

  return (
    <section className={styles.panel} aria-labelledby={`${formId}-title`}>
      <header className={styles.heading}>
        <div>
          <span>Analyst tools</span>
          <h2 id={`${formId}-title`}>Telemetry comparison</h2>
        </div>
        <small>Up to {MAX_DRIVERS} cars · retained samples only</small>
      </header>

      <form className={styles.form} onSubmit={compare}>
        <fieldset className={styles.fieldset}>
          <legend>Cars</legend>
          <div className={styles.options}>
            {drivers.length ? (
              drivers.map((driver) => {
                const checked = selected.includes(driver.driverNumber);
                return (
                  <label key={driver.driverNumber} className={styles.option}>
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={!checked && atLimit}
                      onChange={() => toggleDriver(driver.driverNumber)}
                    />
                    <span>{driver.name}</span>
                  </label>
                );
              })
            ) : (
              <p className={styles.empty}>No classified cars are available to compare yet.</p>
            )}
          </div>
        </fieldset>
        <div className={styles.lap}>
          <label htmlFor={`${formId}-lap`}>Lap</label>
          <input
            id={`${formId}-lap`}
            type="number"
            min={0}
            inputMode="numeric"
            value={lapInput}
            placeholder={currentLap == null ? "Latest" : String(currentLap)}
            aria-describedby={`${formId}-lap-hint`}
            onChange={(event) => setLapInput(event.target.value)}
          />
          <small id={`${formId}-lap-hint`}>Leave empty for the latest retained window.</small>
        </div>
        {/*
          The label never changes while a read is in flight, and the button stays
          usable, so a reader can supersede a slow request. The live region below
          reports progress instead.
        */}
        <button
          type="submit"
          className={styles.submit}
          disabled={!selected.length}
          aria-busy={request.status === "loading"}
        >
          Compare telemetry
        </button>
      </form>

      <div aria-live="polite">
        {request.status === "idle" ? (
          <p className={styles.empty}>
            Pick one or two cars and compare. Telemetry is read on request, never on the live
            timing tick.
          </p>
        ) : null}
        {request.status === "loading" ? (
          <p className={styles.empty}>Reading retained telemetry…</p>
        ) : null}
        {request.status === "error" ? (
          <p className={styles.error} role="alert">{request.message}</p>
        ) : null}
        {request.status === "ready" ? (
          <TelemetryResult window={request.window} drivers={drivers} />
        ) : null}
      </div>
    </section>
  );
}
