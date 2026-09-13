// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import { rankBattleCards } from "@/lib/race-intelligence";
import type { BattleState, DriverRaceState, RaceRoomMode } from "@/lib/types";

import { AnalystBattleContext, FanBattleContext } from "./battle-strategy-context";
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

function DriverButton({
  position,
  name,
  tyre,
  selected,
  onSelect,
}: {
  position: number;
  name: string;
  tyre: string | null;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button type="button" aria-pressed={selected} aria-label={`Select ${name}`} onClick={onSelect}>
      <strong>P{position}</strong>
      <span>{shortName(name)}</span>
      {tyre ? <small>{tyre}</small> : null}
    </button>
  );
}

/**
 * Analyst detail for one battle.
 *
 * The timing summary is always true of any battle. The engine's published
 * context is added when it exists; when it does not, the card says the engine
 * published none rather than leaving a blank drawer.
 */
function BattleEvidence({
  battle,
  resolveName,
}: {
  battle: BattleState;
  resolveName: (driverNumber: number) => string;
}) {
  const context = battle.strategy_context;
  return (
    <details className={styles.evidence}>
      <summary>Battle evidence</summary>
      <p className={styles.summaryLine}>
        Closest {battle.closest_interval_seconds.toFixed(2)}s ·{" "}
        {battle.interval_history.length} timing samples ·{" "}
        {battle.trend.toLowerCase().replace("_", " ")}
      </p>
      {context ? (
        <AnalystBattleContext
          context={context}
          leadDriverNumber={battle.lead_driver_number}
          chasingDriverNumber={battle.chasing_driver_number}
          driverName={resolveName}
        />
      ) : (
        <p className={styles.noContext}>
          The Battle Engine published no strategy context at this cursor, so only the timing
          summary above is available for this fight.
        </p>
      )}
    </details>
  );
}

function BattleCard({
  battle,
  drivers,
  currentLap,
  selectedDriver,
  mode,
  onSelectDriver,
}: {
  battle: BattleState;
  drivers: Record<string, DriverRaceState>;
  currentLap: number | null;
  selectedDriver: number | null;
  mode: RaceRoomMode;
  onSelectDriver: (driverNumber: number) => void;
}) {
  const resolveName = (number: number) => driverName(drivers, number);
  const leaderName = resolveName(battle.lead_driver_number);
  const chaserName = resolveName(battle.chasing_driver_number);
  const context = battle.strategy_context;

  return (
    <article
      className={`${styles.card} ${styles[`intensity_${battle.intensity.toLowerCase()}`]}`}
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
        <DriverButton
          position={battle.lead_position}
          name={leaderName}
          tyre={tyreLabel(drivers[String(battle.lead_driver_number)], currentLap)}
          selected={selectedDriver === battle.lead_driver_number}
          onSelect={() => onSelectDriver(battle.lead_driver_number)}
        />
        <span className={styles.interval} aria-hidden>vs</span>
        <DriverButton
          position={battle.chasing_position}
          name={chaserName}
          tyre={tyreLabel(drivers[String(battle.chasing_driver_number)], currentLap)}
          selected={selectedDriver === battle.chasing_driver_number}
          onSelect={() => onSelectDriver(battle.chasing_driver_number)}
        />
      </div>
      <p className={styles.meaning}>{trendText(battle, chaserName)}</p>
      <div className={styles.tags}>
        {battle.within_one_second ? <span>Within one second</span> : null}
        {battle.train_size > 2 ? <span>{battle.train_size}-car train</span> : null}
      </div>
      {mode === "FAN" && context ? (
        <FanBattleContext
          context={context}
          leadDriverNumber={battle.lead_driver_number}
          chasingDriverNumber={battle.chasing_driver_number}
          driverName={resolveName}
        />
      ) : null}
      {mode === "ANALYST" ? <BattleEvidence battle={battle} resolveName={resolveName} /> : null}
    </article>
  );
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
          {ranked.map((battle) => (
            <BattleCard
              key={battle.id}
              battle={battle}
              drivers={drivers}
              currentLap={currentLap}
              selectedDriver={selectedDriver}
              mode={mode}
              onSelectDriver={onSelectDriver}
            />
          ))}
        </div>
      ) : (
        <p className={styles.empty}>No sustained close fight is active right now.</p>
      )}
    </section>
  );
}
