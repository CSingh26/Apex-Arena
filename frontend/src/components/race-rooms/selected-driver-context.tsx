// SPDX-License-Identifier: AGPL-3.0-only
import type { DriverBattleContext, DriverTimingState, RaceRoomMode } from "@/lib/types";

import styles from "./selected-driver-context.module.css";

type SelectedDriverContextProps = {
  driverNumber: number | null;
  timing: DriverTimingState[];
  context: DriverBattleContext | null;
  mode: RaceRoomMode;
};

function interval(value: number | null): string {
  return value == null ? "Gap unavailable" : `${value.toFixed(2)}s`;
}

export function SelectedDriverContext({
  driverNumber,
  timing,
  context,
  mode,
}: SelectedDriverContextProps) {
  const selected = timing.find((driver) => driver.driver_number === driverNumber);
  const ahead = timing.find((driver) => driver.driver_number === context?.ahead_driver_number);
  const behind = timing.find((driver) => driver.driver_number === context?.behind_driver_number);
  const status = context?.status ?? "UNAVAILABLE";
  return (
    <section className={styles.context} aria-labelledby="selected-context-title">
      <header>
        <div>
          <span>Selected driver</span>
          <h2 id="selected-context-title">{selected?.name ?? (driverNumber ? `Car ${driverNumber}` : "Choose a driver")}</h2>
        </div>
        {selected?.position ? <b>P{selected.position}</b> : null}
      </header>
      <p className={`${styles.status} ${styles[`status_${status.toLowerCase()}`]}`}>{status.replace("_", " ")}</p>
      <dl>
        {ahead ? <div><dt>Ahead</dt><dd>{ahead.name.split(" ").at(-1)} · {interval(context?.ahead_interval_seconds ?? null)}</dd></div> : null}
        {behind ? <div><dt>Behind</dt><dd>{behind.name.split(" ").at(-1)} · {interval(context?.behind_interval_seconds ?? null)}</dd></div> : null}
      </dl>
      {mode === "ANALYST" && selected ? (
        <div className={styles.analyst}>
          <span>Latest {selected.latest_lap == null ? "unavailable" : `${selected.latest_lap.toFixed(3)}s`}</span>
          <span>Best {selected.best_lap == null ? "unavailable" : `${selected.best_lap.toFixed(3)}s`}</span>
          <span>{selected.tyre_compound} · {selected.tyre_age_laps == null ? "age unavailable" : `${selected.tyre_age_laps} laps`}</span>
        </div>
      ) : null}
    </section>
  );
}
