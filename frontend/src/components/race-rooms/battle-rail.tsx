// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import { rankBattleCards } from "@/lib/race-intelligence";
import type { BattleState, DriverRaceState, RaceRoomMode } from "@/lib/types";

import styles from "./battle-rail.module.css";

type BattleRailProps = {
  battles: BattleState[];
  drivers: Record<string, DriverRaceState>;
  currentLap: number | null;
  selectedDriver: number | null;
  mode: RaceRoomMode;
  onSelectDriver: (driverNumber: number) => void;
};

function driverName(drivers: Record<string, DriverRaceState>, number: number): string {
  const driver = drivers[String(number)];
  return driver?.full_name ?? driver?.broadcast_name ?? `Car ${number}`;
}

function shortName(name: string): string {
  return name.split(" ").at(-1) ?? name;
}

function tyreLabel(
  driver: DriverRaceState | undefined,
  currentLap: number | null,
): string | null {
  if (!driver) return null;
  const compound = typeof driver.stint.compound === "string"
    ? driver.stint.compound.toLowerCase().replace(/^./, (letter) => letter.toUpperCase())
    : null;
  const start = typeof driver.stint.lap_start === "number"
    ? driver.stint.lap_start
    : typeof driver.stint.start_lap === "number"
      ? driver.stint.start_lap
      : null;
  const age = start != null && currentLap != null && currentLap >= start
    ? currentLap - start + 1
    : null;
  if (!compound && age == null) return null;
  return [compound, age == null ? null : `${age} ${age === 1 ? "lap" : "laps"}`]
    .filter(Boolean)
    .join(" · ");
}

function trendText(battle: BattleState, chaser: string): string {
  if (battle.trend === "CLOSING") return `${shortName(chaser)} is closing`;
  if (battle.trend === "FALLING_BACK") return `${shortName(chaser)} is falling back`;
  return "The interval is stable";
}

export function BattleRail({
  battles,
  drivers,
  currentLap,
  selectedDriver,
  mode,
  onSelectDriver,
}: BattleRailProps) {
  const ranked = rankBattleCards(battles, selectedDriver);
  return (
    <section className={styles.rail} aria-labelledby="battle-rail-title">
      <header className={styles.heading}>
        <div>
          <span>Race intelligence</span>
          <h2 id="battle-rail-title">Current battles</h2>
        </div>
        <small>{ranked.length ? `${ranked.length} prioritized` : "Monitoring intervals"}</small>
      </header>
      {ranked.length ? (
        <div className={styles.cards}>
          {ranked.map((battle) => {
            const leaderName = driverName(drivers, battle.lead_driver_number);
            const chaserName = driverName(drivers, battle.chasing_driver_number);
            const leaderTyre = tyreLabel(drivers[String(battle.lead_driver_number)], currentLap);
            const chaserTyre = tyreLabel(drivers[String(battle.chasing_driver_number)], currentLap);
            return (
              <article
                className={`${styles.card} ${styles[`intensity_${battle.intensity.toLowerCase()}`]}`}
                key={battle.id}
                aria-label={`Battle for position ${battle.lead_position}`}
              >
                <header>
                  <div>
                    <span>{battle.intensity}</span>
                    <h3>Battle for P{battle.lead_position}</h3>
                  </div>
                  <b>{battle.interval_seconds.toFixed(2)}s</b>
                </header>
                <div className={styles.matchup}>
                  <button
                    type="button"
                    aria-pressed={selectedDriver === battle.lead_driver_number}
                    aria-label={`Select ${leaderName}`}
                    onClick={() => onSelectDriver(battle.lead_driver_number)}
                  >
                    <strong>P{battle.lead_position}</strong>
                    <span>{shortName(leaderName)}</span>
                    {leaderTyre ? <small>{leaderTyre}</small> : null}
                  </button>
                  <span className={styles.interval} aria-hidden>vs</span>
                  <button
                    type="button"
                    aria-pressed={selectedDriver === battle.chasing_driver_number}
                    aria-label={`Select ${chaserName}`}
                    onClick={() => onSelectDriver(battle.chasing_driver_number)}
                  >
                    <strong>P{battle.chasing_position}</strong>
                    <span>{shortName(chaserName)}</span>
                    {chaserTyre ? <small>{chaserTyre}</small> : null}
                  </button>
                </div>
                <p className={styles.meaning}>{trendText(battle, chaserName)}</p>
                <div className={styles.tags}>
                  {battle.within_one_second ? <span>Within one second</span> : null}
                  {battle.train_size > 2 ? <span>{battle.train_size}-car train</span> : null}
                </div>
                {mode === "ANALYST" ? (
                  <details className={styles.evidence}>
                    <summary>Battle evidence</summary>
                    <p>
                      Closest {battle.closest_interval_seconds.toFixed(2)}s · {battle.interval_history.length} timing samples · {battle.trend.toLowerCase().replace("_", " ")}
                    </p>
                  </details>
                ) : null}
              </article>
            );
          })}
        </div>
      ) : (
        <p className={styles.empty}>No sustained close fight is active right now.</p>
      )}
    </section>
  );
}
