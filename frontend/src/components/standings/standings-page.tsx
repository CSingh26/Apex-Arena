// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import Image from "next/image";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AppNavigation } from "@/components/navigation/app-navigation";
import { getConstructorStandings, getDriverStandings } from "@/lib/api";
import type { ChampionshipMetadata, ConstructorStanding, DriverStanding } from "@/lib/types";

type Tab = "drivers" | "constructors";
type DataState = {
  drivers: DriverStanding[];
  constructors: ConstructorStanding[];
  metadata: ChampionshipMetadata;
};

function number(value: number | null | undefined, digits = 0): string {
  return value == null || !Number.isFinite(value) ? "—" : value.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function optionalNumber(value: number | null | undefined, digits = 0): string | null {
  return value == null || !Number.isFinite(value) ? null : number(value, digits);
}

function statLabel(value: number | null | undefined, singular: string, plural = `${singular}s`): string | null {
  return value == null ? null : `${number(value)} ${value === 1 ? singular : plural}`;
}

function colour(value: string | null | undefined): string {
  if (!value) return "#697586";
  return /^#?[0-9a-f]{6}$/i.test(value) ? `#${value.replace("#", "")}` : "#697586";
}

export function movementLabel(value: number | null): string {
  if (value == null) return "Movement unavailable";
  if (value > 0) return `Up ${value} ${value === 1 ? "place" : "places"}`;
  if (value < 0) return `Down ${Math.abs(value)} ${value === -1 ? "place" : "places"}`;
  return "No position change";
}

function Movement({ value }: { value: number | null }) {
  const direction = value == null ? "unknown" : value > 0 ? "up" : value < 0 ? "down" : "same";
  const visible = value == null ? "—" : value > 0 ? `↑ ${value}` : value < 0 ? `↓ ${Math.abs(value)}` : "— Same";
  return <span className={`standing-movement standing-movement--${direction}`} aria-label={movementLabel(value)}>{visible}</span>;
}

function DriverPortrait({ driver }: { driver: DriverStanding }) {
  const [failed, setFailed] = useState(false);
  const initials = driver.acronym || `${driver.first_name?.[0] ?? ""}${driver.last_name?.[0] ?? ""}` || String(driver.driver_number ?? "?");
  if (!driver.headshot_url || failed) return <span className="driver-portrait driver-portrait--fallback" aria-label={`${driver.full_name} portrait unavailable`}>{initials}</span>;
  return <span className="driver-portrait"><Image src={driver.headshot_url} alt={`${driver.full_name} headshot`} width={62} height={62} sizes="62px" unoptimized onError={() => setFailed(true)} /></span>;
}

function ConstructorLogo({ constructor }: { constructor: ConstructorStanding }) {
  const [failed, setFailed] = useState(false);
  const fallback = constructor.team_name.slice(0, 2).toUpperCase();
  if (!constructor.logo_url || failed) {
    return <span className="constructor-mark constructor-mark--fallback" aria-label={`${constructor.team_name} logo unavailable`}>{fallback}</span>;
  }
  return <span className="constructor-mark"><Image src={constructor.logo_url} alt={`${constructor.team_name} logo`} width={72} height={48} sizes="72px" unoptimized onError={() => setFailed(true)} /></span>;
}

function Stat({ label, value }: { label: string; value: string | null }) {
  if (value == null) return null;
  return <div className="standing-stat"><dt>{label}</dt><dd>{value}</dd></div>;
}

function DriverDetails({ driver }: { driver: DriverStanding }) {
  const pointsPerRace = driver.points_per_race ?? (driver.race_starts && driver.points != null ? driver.points / driver.race_starts : null);
  const hasSprint = [driver.sprint_starts, driver.sprint_wins, driver.sprint_podiums, driver.sprint_points].some((value) => value != null && value > 0);
  const hasRacePerformance = [driver.race_starts, driver.average_finish, driver.best_finish, driver.dnfs, pointsPerRace].some((value) => value != null);
  const hasQualifying = [driver.poles, driver.average_qualifying_position, driver.average_grid_position, driver.best_qualifying_result, driver.q3_appearances].some((value) => value != null);
  return <div className="standing-details">
    <section><h4>Season</h4><dl><Stat label="Points" value={number(driver.points)} /><Stat label="Wins" value={optionalNumber(driver.wins)} /><Stat label="Podiums" value={optionalNumber(driver.podiums)} /><Stat label="Poles" value={optionalNumber(driver.poles)} /><Stat label="Fastest laps" value={optionalNumber(driver.fastest_laps)} /></dl></section>
    {hasRacePerformance && <section><h4>Race performance</h4><dl><Stat label="Starts" value={optionalNumber(driver.race_starts)} /><Stat label="Average finish" value={driver.average_finish == null ? null : `P${number(driver.average_finish, 1)}`} /><Stat label="Best finish" value={driver.best_finish == null ? null : `P${driver.best_finish}`} /><Stat label="DNFs" value={optionalNumber(driver.dnfs)} /><Stat label="Points / race" value={pointsPerRace == null ? null : number(pointsPerRace, 1)} /></dl></section>}
    {hasQualifying && <section><h4>Qualifying</h4><dl><Stat label="Poles" value={optionalNumber(driver.poles)} /><Stat label="Average qualifying" value={(driver.average_qualifying_position ?? driver.average_grid_position) == null ? null : `P${number(driver.average_qualifying_position ?? driver.average_grid_position, 1)}`} /><Stat label="Best qualifying" value={driver.best_qualifying_result == null ? null : `P${driver.best_qualifying_result}`} /><Stat label="Q3 appearances" value={optionalNumber(driver.q3_appearances)} /></dl></section>}
    {hasSprint && <section><h4>Sprint</h4><dl><Stat label="Starts" value={optionalNumber(driver.sprint_starts)} /><Stat label="Wins" value={optionalNumber(driver.sprint_wins)} /><Stat label="Podiums" value={optionalNumber(driver.sprint_podiums)} /><Stat label="Points" value={optionalNumber(driver.sprint_points)} /></dl></section>}
  </div>;
}

function DriverRow({ driver }: { driver: DriverStanding }) {
  const [expanded, setExpanded] = useState(false);
  const highlights = [statLabel(driver.wins, "win"), statLabel(driver.podiums, "podium")].filter(Boolean).join(" · ");
  return <article className={`standing-row ${expanded ? "standing-row--expanded" : ""}`} style={{ "--team-colour": colour(driver.team_colour) } as React.CSSProperties}>
    <button className="standing-row__summary" type="button" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>
      <strong className="standing-position"><small>P</small>{driver.position}</strong>
      <DriverPortrait driver={driver} />
      <span className="standing-identity"><b>{driver.full_name}</b><small>{[driver.acronym, driver.driver_number == null ? null : `#${driver.driver_number}`].filter(Boolean).join(" · ")}</small><span><i aria-hidden />{driver.team_name ?? "Team unavailable"}</span></span>
      <span className="standing-form">{highlights || "Season statistics pending"}</span>
      <span className="standing-points"><b>{number(driver.points)}</b><small>PTS</small></span>
      <Movement value={driver.championship_position_change} />
      <span className="standing-chevron" aria-hidden>{expanded ? "−" : "+"}</span>
    </button>
    {expanded && <DriverDetails driver={driver} />}
  </article>;
}

function ConstructorDetails({ constructor }: { constructor: ConstructorStanding }) {
  const maxPoints = Math.max(...constructor.drivers.map((driver) => driver.points ?? 0), 0);
  const hasPerformance = [constructor.average_finish, constructor.dnfs, constructor.sprint_wins, constructor.points_change_from_previous_race, constructor.average_points_per_event].some((value) => value != null);
  return <div className="standing-details standing-details--constructor">
    <section><h4>Season</h4><dl><Stat label="Points" value={number(constructor.points)} /><Stat label="Wins" value={optionalNumber(constructor.wins)} /><Stat label="Podiums" value={optionalNumber(constructor.podiums)} /><Stat label="Poles" value={optionalNumber(constructor.poles)} /><Stat label="Fastest laps" value={optionalNumber(constructor.fastest_laps)} /><Stat label="Double podiums" value={optionalNumber(constructor.double_podiums)} /></dl></section>
    {hasPerformance && <section><h4>Performance</h4><dl><Stat label="Average finish" value={constructor.average_finish == null ? null : `P${number(constructor.average_finish, 1)}`} /><Stat label="DNFs" value={optionalNumber(constructor.dnfs)} /><Stat label="Sprint wins" value={optionalNumber(constructor.sprint_wins)} /><Stat label="Latest event" value={constructor.points_change_from_previous_race == null ? null : `${number(constructor.points_change_from_previous_race)} pts`} /><Stat label="Points / event" value={constructor.average_points_per_event == null ? null : number(constructor.average_points_per_event, 1)} /></dl></section>}
    {constructor.drivers.some((driver) => driver.points != null) && <section className="driver-contributions"><h4>Driver contributions</h4>{constructor.drivers.map((driver) => <div key={`${constructor.constructor_id}-${driver.driver_number}`}><span><b>{driver.full_name}</b><small>{number(driver.points)} pts</small></span><i><span style={{ width: `${maxPoints ? ((driver.points ?? 0) / maxPoints) * 100 : 0}%` }} /></i></div>)}</section>}
  </div>;
}

function ConstructorRow({ constructor }: { constructor: ConstructorStanding }) {
  const [expanded, setExpanded] = useState(false);
  const highlights = [statLabel(constructor.wins, "win"), statLabel(constructor.podiums, "podium")].filter(Boolean).join(" · ");
  return <article className={`standing-row constructor-row ${expanded ? "standing-row--expanded" : ""}`} style={{ "--team-colour": colour(constructor.team_colour) } as React.CSSProperties}>
    <button className="standing-row__summary" type="button" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>
      <strong className="standing-position"><small>P</small>{constructor.position}</strong>
      <ConstructorLogo constructor={constructor} />
      <span className="standing-identity"><b>{constructor.team_name}</b><small>{constructor.drivers.map((driver) => driver.acronym || driver.full_name).join(" · ") || "Drivers pending"}</small><span><i aria-hidden />2026 constructor</span></span>
      <span className="standing-form">{highlights || "Season statistics pending"}</span>
      <span className="standing-points"><b>{number(constructor.points)}</b><small>PTS</small></span>
      <Movement value={constructor.championship_position_change} />
      <span className="standing-chevron" aria-hidden>{expanded ? "−" : "+"}</span>
    </button>
    {expanded && <ConstructorDetails constructor={constructor} />}
  </article>;
}

function LeaderHero({ data }: { data: DataState }) {
  const driver = data.drivers[0];
  const constructor = data.constructors[0];
  if (!driver && !constructor) return null;
  const gap = (leader: { points: number } | undefined, second: { points: number } | undefined) => leader && second ? leader.points - second.points : null;
  return <section className="championship-hero" aria-label="Championship leaders">
    {driver && <article style={{ "--team-colour": colour(driver.team_colour) } as React.CSSProperties}><div><p>Drivers&apos; championship</p><span>P1</span></div><div className="championship-hero__leader"><DriverPortrait driver={driver} /><span><b>{driver.full_name}</b><small>{number(driver.points)} points</small></span></div><p>{gap(driver, data.drivers[1]) == null ? "Advantage pending" : `+${number(gap(driver, data.drivers[1]))} ahead of P2`}</p></article>}
    {constructor && <article style={{ "--team-colour": colour(constructor.team_colour) } as React.CSSProperties}><div><p>Constructors&apos; championship</p><span>P1</span></div><div className="championship-hero__leader"><ConstructorLogo constructor={constructor} /><span><b>{constructor.team_name}</b><small>{number(constructor.points)} points</small></span></div><p>{gap(constructor, data.constructors[1]) == null ? "Advantage pending" : `+${number(gap(constructor, data.constructors[1]))} ahead of P2`}</p></article>}
  </section>;
}

function StandingsSkeleton() {
  return <div className="standings-skeleton" role="status"><span className="sr-only">Loading championship standings</span>{Array.from({ length: 7 }, (_, index) => <i key={index} />)}</div>;
}

export function StandingsPage() {
  const [tab, setTab] = useState<Tab>("drivers");
  const [data, setData] = useState<DataState | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [partial, setPartial] = useState(false);
  const dataRef = useRef<DataState | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    if (dataRef.current) setRefreshing(true); else setLoading(true);
    try {
      const [driversResult, constructorsResult] = await Promise.allSettled([getDriverStandings(signal), getConstructorStandings(signal)]);
      if (driversResult.status === "rejected" && constructorsResult.status === "rejected") throw driversResult.reason;
      const previous = dataRef.current;
      const driverResponse = driversResult.status === "fulfilled" ? driversResult.value : null;
      const constructorResponse = constructorsResult.status === "fulfilled" ? constructorsResult.value : null;
      const nextData = {
        drivers: [...(driverResponse?.standings ?? previous?.drivers ?? [])].sort((a, b) => a.position - b.position),
        constructors: [...(constructorResponse?.standings ?? previous?.constructors ?? [])].sort((a, b) => a.position - b.position),
        metadata: driverResponse?.metadata ?? constructorResponse!.metadata,
      };
      dataRef.current = nextData;
      setData(nextData);
      const isPartial = !driverResponse || !constructorResponse;
      setPartial(isPartial);
      setError(isPartial ? "One championship feed is temporarily unavailable." : null);
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      setPartial(false);
      setError(reason instanceof Error ? reason.message : "Championship data is temporarily unavailable.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  useEffect(() => {
    if (!dataRef.current) return;
    const interval = window.setInterval(() => void load(), dataRef.current.metadata.live ? 20_000 : 300_000);
    return () => window.clearInterval(interval);
  }, [data?.metadata.live, load]);

  const visible = useMemo(() => tab === "drivers" ? data?.drivers ?? [] : data?.constructors ?? [], [data, tab]);
  const generated = data?.metadata.generated_at ? new Date(data.metadata.generated_at) : null;
  const generatedLabel = generated && !Number.isNaN(generated.getTime()) ? generated.toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }) : null;

  return <main className="standings-shell track-grid">
    <AppNavigation />
    <header className="standings-header"><div><p className="section-kicker">2026 season</p><h1>Championship<br /><em>standings.</em></h1><p>Every point, podium and position in the current title fight—updated from official race data.</p></div>{data && <aside><span className={data.metadata.provisional ? "standings-status standings-status--live" : "standings-status"}>{data.metadata.provisional ? "Live / provisional" : "Current standings"}</span><small>{data.metadata.latest_completed_event ? `After ${data.metadata.latest_completed_event}` : `${data.metadata.races_completed} races complete`}{generatedLabel ? ` · Updated ${generatedLabel}` : ""}</small></aside>}</header>
    {loading && <StandingsSkeleton />}
    {!loading && !data && <section className="standings-unavailable"><p className="section-kicker">Data unavailable</p><h2>The championship feed is taking a pit stop.</h2><p>{error ?? "Standings have not been published yet. Please try again shortly."}</p><button type="button" onClick={() => void load()}>Try again</button></section>}
    {data && <>
      {(error || data.metadata.stale) && <div className="standings-stale" role="status"><span>{partial ? "Some championship data is temporarily unavailable" : "Using last available standings"}</span><button type="button" onClick={() => void load()}>Retry</button></div>}
      <LeaderHero data={data} />
      <section className="standings-board" aria-labelledby="standings-board-title">
        <div className="standings-tabs" role="tablist" aria-label="Championship category"><button id="drivers-tab" role="tab" aria-selected={tab === "drivers"} aria-controls="drivers-panel" type="button" onClick={() => setTab("drivers")}>Drivers <span>{data.drivers.length}</span></button><button id="constructors-tab" role="tab" aria-selected={tab === "constructors"} aria-controls="constructors-panel" type="button" onClick={() => setTab("constructors")}>Constructors <span>{data.constructors.length}</span></button>{refreshing && <small role="status">Updating…</small>}</div>
        <div className="standings-board__heading"><div><p className="section-kicker">World championship</p><h2 id="standings-board-title">{tab === "drivers" ? "Driver standings" : "Constructor standings"}</h2></div>{data.metadata.races_remaining != null && <span>{data.metadata.races_remaining} races remaining</span>}</div>
        <div id={`${tab}-panel`} role="tabpanel" aria-labelledby={`${tab}-tab`} className="standings-list">
          {tab === "drivers" ? data.drivers.map((driver) => <DriverRow driver={driver} key={driver.driver_id || driver.driver_number} />) : data.constructors.map((constructor) => <ConstructorRow constructor={constructor} key={constructor.constructor_id || constructor.team_id || constructor.team_name} />)}
          {!visible.length && <div className="standings-empty"><b>No standings published yet</b><p>The championship will appear here as soon as official data is available.</p></div>}
        </div>
      </section>
    </>}
  </main>;
}
