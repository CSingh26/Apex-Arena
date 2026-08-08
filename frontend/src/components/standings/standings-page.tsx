// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import Image from "next/image";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import { AppNavigation } from "@/components/navigation/app-navigation";
import { getConstructorStandings, getDriverStandings } from "@/lib/api";
import type { ChampionshipMetadata, ConstructorStanding, DriverStanding } from "@/lib/types";

import styles from "./standings-page.module.css";

type Tab = "drivers" | "constructors";
type DataState = { drivers: DriverStanding[]; constructors: ConstructorStanding[]; metadata: ChampionshipMetadata };

function formatNumber(value: number | null | undefined, digits = 0): string {
  return value == null || !Number.isFinite(value) ? "—" : value.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function optionalNumber(value: number | null | undefined, digits = 0): string | null {
  return value == null || !Number.isFinite(value) ? null : formatNumber(value, digits);
}

function pluralStat(value: number | null | undefined, singular: string, plural = `${singular}s`): string | null {
  return value == null ? null : `${formatNumber(value)} ${value === 1 ? singular : plural}`;
}

function teamColour(value: string | null | undefined): string {
  if (!value) return "#7d8796";
  return /^#?[0-9a-f]{6}$/i.test(value) ? `#${value.replace("#", "")}` : "#7d8796";
}

export function movementLabel(value: number | null): string {
  if (value == null) return "Movement unavailable";
  if (value > 0) return `Up ${value} ${value === 1 ? "place" : "places"}`;
  if (value < 0) return `Down ${Math.abs(value)} ${value === -1 ? "place" : "places"}`;
  return "No position change";
}

function Movement({ value }: { value: number | null }) {
  const tone = value == null ? styles.movementUnknown : value > 0 ? styles.movementUp : value < 0 ? styles.movementDown : styles.movementSame;
  const visible = value == null ? "—" : value > 0 ? `↑ ${value}` : value < 0 ? `↓ ${Math.abs(value)}` : "—";
  return <span className={`${styles.movement} ${tone}`} aria-label={movementLabel(value)}><span>{visible}</span><small>{value === 0 ? "Held" : "Since last race"}</small></span>;
}

function DriverPortrait({ driver, featured = false }: { driver: DriverStanding; featured?: boolean }) {
  const [failed, setFailed] = useState(false);
  const initials = driver.acronym || `${driver.first_name?.[0] ?? ""}${driver.last_name?.[0] ?? ""}` || String(driver.driver_number ?? "?");
  const className = `${styles.portrait} ${featured ? styles.portraitFeatured : ""}`;
  if (!driver.headshot_url || failed) return <span className={`${className} ${styles.mediaFallback}`} aria-label={`${driver.full_name} portrait unavailable`}>{initials}</span>;
  return <span className={className}><Image src={driver.headshot_url} alt={`${driver.full_name} headshot`} fill sizes={featured ? "(max-width: 720px) 88px, 120px" : "72px"} unoptimized onError={() => setFailed(true)} /></span>;
}

function ConstructorLogo({ constructor, featured = false }: { constructor: ConstructorStanding; featured?: boolean }) {
  const [failed, setFailed] = useState(false);
  const className = `${styles.constructorLogo} ${featured ? styles.constructorLogoFeatured : ""}`;
  if (!constructor.logo_url || failed) return <span className={`${className} ${styles.mediaFallback}`} aria-label={`${constructor.team_name} logo unavailable`}>{constructor.team_name.slice(0, 2).toUpperCase()}</span>;
  return <span className={className}><Image src={constructor.logo_url} alt={`${constructor.team_name} logo`} fill sizes={featured ? "130px" : "88px"} unoptimized onError={() => setFailed(true)} /></span>;
}

function Stat({ label, value, prominent = false }: { label: string; value: string | null; prominent?: boolean }) {
  if (value == null) return null;
  return <div className={`${styles.stat} ${prominent ? styles.statProminent : ""}`}><dt>{label}</dt><dd>{value}</dd></div>;
}

function DetailSection({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className={styles.detailSection}><h4>{title}</h4><dl>{children}</dl></section>;
}

function DriverDetails({ driver, id }: { driver: DriverStanding; id: string }) {
  const pointsPerRace = driver.points_per_race ?? (driver.race_starts ? driver.points / driver.race_starts : null);
  const hasSprint = [driver.sprint_starts, driver.sprint_wins, driver.sprint_podiums, driver.sprint_points].some((value) => value != null && value > 0);
  const hasRace = [driver.race_starts, driver.average_finish, driver.best_finish, driver.dnfs, pointsPerRace].some((value) => value != null);
  const hasQualifying = [driver.poles, driver.average_qualifying_position, driver.average_grid_position, driver.best_qualifying_result, driver.q3_appearances].some((value) => value != null);
  const averageQualifying = driver.average_qualifying_position ?? driver.average_grid_position;
  return <div className={styles.details} id={id} role="region" aria-label={`${driver.full_name} season details`}>
    <DetailSection title="Championship"><Stat label="Points" value={formatNumber(driver.points)} prominent /><Stat label="Wins" value={optionalNumber(driver.wins)} /><Stat label="Podiums" value={optionalNumber(driver.podiums)} /><Stat label="Poles" value={optionalNumber(driver.poles)} /><Stat label="Fastest laps" value={optionalNumber(driver.fastest_laps)} /></DetailSection>
    {hasRace && <DetailSection title="Race pace"><Stat label="Starts" value={optionalNumber(driver.race_starts)} /><Stat label="Average finish" value={driver.average_finish == null ? null : `P${formatNumber(driver.average_finish, 1)}`} /><Stat label="Best result" value={driver.best_finish == null ? null : `P${driver.best_finish}`} /><Stat label="DNFs" value={optionalNumber(driver.dnfs)} /><Stat label="Points / race" value={optionalNumber(pointsPerRace, 1)} /></DetailSection>}
    {hasQualifying && <DetailSection title="Qualifying"><Stat label="Poles" value={optionalNumber(driver.poles)} /><Stat label="Average result" value={averageQualifying == null ? null : `P${formatNumber(averageQualifying, 1)}`} /><Stat label="Best result" value={driver.best_qualifying_result == null ? null : `P${driver.best_qualifying_result}`} /><Stat label="Q3 appearances" value={optionalNumber(driver.q3_appearances)} /></DetailSection>}
    {hasSprint && <DetailSection title="Sprint"><Stat label="Starts" value={optionalNumber(driver.sprint_starts)} /><Stat label="Wins" value={optionalNumber(driver.sprint_wins)} /><Stat label="Podiums" value={optionalNumber(driver.sprint_podiums)} /><Stat label="Points" value={optionalNumber(driver.sprint_points)} /></DetailSection>}
  </div>;
}

function DriverRow({ driver }: { driver: DriverStanding }) {
  const [expanded, setExpanded] = useState(false);
  const detailId = useId();
  const highlights = [pluralStat(driver.wins, "win"), pluralStat(driver.podiums, "podium")].filter(Boolean).join(" · ");
  return <article className={`${styles.row} ${driver.position <= 3 ? styles.rowPodium : ""} ${expanded ? styles.rowExpanded : ""}`} style={{ "--team": teamColour(driver.team_colour) } as React.CSSProperties} data-testid="driver-standing">
    <button className={styles.rowButton} type="button" aria-expanded={expanded} aria-controls={detailId} onClick={() => setExpanded((current) => !current)}>
      <span className={styles.position}><small>Position</small><b>{String(driver.position).padStart(2, "0")}</b></span>
      <DriverPortrait driver={driver} />
      <span className={styles.identity}><span className={styles.identityName}>{driver.full_name}</span><span className={styles.identityMeta}>{[driver.acronym, driver.driver_number == null ? null : `#${driver.driver_number}`].filter(Boolean).join(" · ")}</span><span className={styles.team}><i aria-hidden />{driver.team_name ?? "Team unavailable"}</span></span>
      <span className={styles.form}><small>Season form</small><b>{highlights || "Statistics pending"}</b></span>
      <span className={styles.points}><b>{formatNumber(driver.points)}</b><small>Points</small></span>
      <Movement value={driver.championship_position_change} />
      <span className={styles.expandIcon} aria-hidden><i /><i /></span>
    </button>
    {expanded && <DriverDetails driver={driver} id={detailId} />}
  </article>;
}

function Contributions({ constructor }: { constructor: ConstructorStanding }) {
  const total = constructor.drivers.reduce((sum, driver) => sum + (driver.points ?? 0), 0);
  return <section className={`${styles.detailSection} ${styles.contributions}`}><h4>Driver contributions</h4><div className={styles.contributionStack}>{constructor.drivers.map((driver) => {
    const share = total > 0 ? ((driver.points ?? 0) / total) * 100 : 0;
    return <div className={styles.contribution} key={driver.driver_id}><span><b>{driver.full_name}</b><small>{optionalNumber(driver.points) ?? "—"} pts</small></span><div aria-label={`${driver.full_name}: ${formatNumber(driver.points)} points`}><i style={{ width: `${share}%` }} /></div></div>;
  })}</div></section>;
}

function ConstructorDetails({ constructor, id }: { constructor: ConstructorStanding; id: string }) {
  const hasPerformance = [constructor.average_finish, constructor.dnfs, constructor.sprint_wins, constructor.points_change_from_previous_race, constructor.average_points_per_event].some((value) => value != null);
  return <div className={`${styles.details} ${styles.constructorDetails}`} id={id} role="region" aria-label={`${constructor.team_name} season details`}>
    <DetailSection title="Championship"><Stat label="Points" value={formatNumber(constructor.points)} prominent /><Stat label="Wins" value={optionalNumber(constructor.wins)} /><Stat label="Podiums" value={optionalNumber(constructor.podiums)} /><Stat label="Poles" value={optionalNumber(constructor.poles)} /><Stat label="Fastest laps" value={optionalNumber(constructor.fastest_laps)} /><Stat label="Double podiums" value={optionalNumber(constructor.double_podiums)} /></DetailSection>
    {hasPerformance && <DetailSection title="Team performance"><Stat label="Average finish" value={constructor.average_finish == null ? null : `P${formatNumber(constructor.average_finish, 1)}`} /><Stat label="DNFs" value={optionalNumber(constructor.dnfs)} /><Stat label="Sprint wins" value={optionalNumber(constructor.sprint_wins)} /><Stat label="Latest event" value={constructor.points_change_from_previous_race == null ? null : `${formatNumber(constructor.points_change_from_previous_race)} pts`} /><Stat label="Points / event" value={optionalNumber(constructor.average_points_per_event, 1)} /></DetailSection>}
    {constructor.drivers.some((driver) => driver.points != null) && <Contributions constructor={constructor} />}
  </div>;
}

function ConstructorRow({ constructor }: { constructor: ConstructorStanding }) {
  const [expanded, setExpanded] = useState(false);
  const detailId = useId();
  const highlights = [pluralStat(constructor.wins, "win"), pluralStat(constructor.podiums, "podium")].filter(Boolean).join(" · ");
  return <article className={`${styles.row} ${styles.constructorRow} ${constructor.position <= 3 ? styles.rowPodium : ""} ${expanded ? styles.rowExpanded : ""}`} style={{ "--team": teamColour(constructor.team_colour) } as React.CSSProperties} data-testid="constructor-standing">
    <button className={styles.rowButton} type="button" aria-expanded={expanded} aria-controls={detailId} onClick={() => setExpanded((current) => !current)}>
      <span className={styles.position}><small>Position</small><b>{String(constructor.position).padStart(2, "0")}</b></span>
      <ConstructorLogo constructor={constructor} />
      <span className={styles.identity}><span className={styles.identityName}>{constructor.team_name}</span><span className={styles.identityMeta}>{constructor.drivers.map((driver) => driver.acronym || driver.full_name).join(" · ") || "Drivers pending"}</span><span className={styles.team}><i aria-hidden />2026 constructor</span></span>
      <span className={styles.form}><small>Season form</small><b>{highlights || "Statistics pending"}</b></span>
      <span className={styles.points}><b>{formatNumber(constructor.points)}</b><small>Points</small></span>
      <Movement value={constructor.championship_position_change} />
      <span className={styles.expandIcon} aria-hidden><i /><i /></span>
    </button>
    {expanded && <ConstructorDetails constructor={constructor} id={detailId} />}
  </article>;
}

function LeaderHero({ data }: { data: DataState }) {
  const driver = data.drivers[0];
  const constructor = data.constructors[0];
  if (!driver && !constructor) return null;
  const gap = (leader: { points: number } | undefined, runnerUp: { points: number } | undefined) => leader && runnerUp ? leader.points - runnerUp.points : null;
  return <section className={styles.leaders} aria-label="Championship leaders">
    {driver && <article className={`${styles.leaderCard} ${styles.driverLeader}`} style={{ "--team": teamColour(driver.team_colour) } as React.CSSProperties}>
      <div className={styles.leaderEyebrow}><span>Drivers&apos; championship</span><b>01</b></div>
      <div className={styles.driverLeaderBody}><DriverPortrait driver={driver} featured /><div><p>Championship leader</p><h2>{driver.full_name}</h2><span>{driver.team_name ?? "Team unavailable"}</span></div></div>
      <div className={styles.leaderScore}><p><b>{formatNumber(driver.points)}</b><span>PTS</span></p><small>{gap(driver, data.drivers[1]) == null ? "Advantage pending" : `+${formatNumber(gap(driver, data.drivers[1]))} over P2`}</small></div>
    </article>}
    {constructor && <article className={`${styles.leaderCard} ${styles.teamLeader}`} style={{ "--team": teamColour(constructor.team_colour) } as React.CSSProperties}>
      <div className={styles.leaderEyebrow}><span>Constructors&apos; championship</span><b>01</b></div>
      <div className={styles.teamLeaderBody}><ConstructorLogo constructor={constructor} featured /><div><p>Leading constructor</p><h2>{constructor.team_name}</h2><span>{constructor.drivers.map((entry) => entry.acronym || entry.full_name).join(" / ")}</span></div></div>
      <div className={styles.leaderScore}><p><b>{formatNumber(constructor.points)}</b><span>PTS</span></p><small>{gap(constructor, data.constructors[1]) == null ? "Advantage pending" : `+${formatNumber(gap(constructor, data.constructors[1]))} over P2`}</small></div>
    </article>}
  </section>;
}

function Skeleton() {
  return <div className={styles.skeleton} role="status"><span className={styles.srOnly}>Loading championship standings</span><div /><div /><div /><div /><div /></div>;
}

export function StandingsPage() {
  const [tab, setTab] = useState<Tab>("drivers");
  const [data, setData] = useState<DataState | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [partial, setPartial] = useState(false);
  const dataRef = useRef<DataState | null>(null);
  const driverTabRef = useRef<HTMLButtonElement>(null);
  const constructorTabRef = useRef<HTMLButtonElement>(null);

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
      setError("We couldn’t reach the championship feed. Check your connection and try again.");
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

  const selectTab = (next: Tab, focus = false) => {
    setTab(next);
    if (focus) (next === "drivers" ? driverTabRef : constructorTabRef).current?.focus();
  };

  const onTabKeyDown = (event: React.KeyboardEvent) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight" && event.key !== "Home" && event.key !== "End") return;
    event.preventDefault();
    selectTab(event.key === "ArrowRight" || event.key === "End" ? "constructors" : "drivers", true);
  };

  return <main className={styles.shell} id="main-content">
    <AppNavigation />
    <div className={styles.content}>
      <header className={styles.header}>
        <div><p className={styles.kicker}><span>Season 2026</span><i aria-hidden /></p><h1>World<br /><em>Standings</em></h1><p className={styles.intro}>The championship picture, distilled. Live positions, season form and every point that shapes the title fight.</p></div>
        {data && <aside className={styles.metadata}><span className={data.metadata.provisional ? styles.liveStatus : styles.currentStatus}><i aria-hidden />{data.metadata.provisional ? "Live · Provisional" : "Current standings"}</span><p>{data.metadata.latest_completed_event ? `After ${data.metadata.latest_completed_event}` : `${data.metadata.races_completed} races complete`}</p>{generatedLabel && <time dateTime={data.metadata.generated_at}>Updated {generatedLabel}</time>}</aside>}
      </header>

      {loading && <Skeleton />}
      {!loading && !data && <section className={styles.unavailable}><span aria-hidden>×</span><p className={styles.kicker}>Data unavailable</p><h2>The championship feed is taking a pit stop.</h2><p>{error ?? "Standings have not been published yet. Please try again shortly."}</p><button type="button" onClick={() => void load()}>Try again <i aria-hidden>↗</i></button></section>}

      {data && <>
        {(error || data.metadata.stale) && <div className={styles.notice} role="status"><div><i aria-hidden>!</i><span><b>{partial ? "Partial standings" : "Last available snapshot"}</b>{partial ? "Some championship data is temporarily unavailable." : "Live data is delayed. Showing the most recent standings."}</span></div><button type="button" onClick={() => void load()}>Retry</button></div>}
        <LeaderHero data={data} />
        <section className={styles.board} aria-labelledby="standings-board-title">
          <div className={styles.boardBar}>
            <div className={styles.tabs} role="tablist" aria-label="Championship category" onKeyDown={onTabKeyDown}>
              <button ref={driverTabRef} id="drivers-tab" role="tab" aria-selected={tab === "drivers"} aria-controls="drivers-panel" tabIndex={tab === "drivers" ? 0 : -1} type="button" onClick={() => selectTab("drivers")}>Drivers <span>{String(data.drivers.length).padStart(2, "0")}</span></button>
              <button ref={constructorTabRef} id="constructors-tab" role="tab" aria-selected={tab === "constructors"} aria-controls="constructors-panel" tabIndex={tab === "constructors" ? 0 : -1} type="button" onClick={() => selectTab("constructors")}>Constructors <span>{String(data.constructors.length).padStart(2, "0")}</span></button>
            </div>
            <div className={styles.seasonProgress}>{refreshing ? <small role="status">Updating live data…</small> : data.metadata.races_remaining != null ? <><b>{data.metadata.races_completed}</b><span>/</span><b>{data.metadata.races_completed + data.metadata.races_remaining}</b><small>Rounds complete</small></> : <small>{data.metadata.races_completed} rounds complete</small>}</div>
          </div>
          <div className={styles.boardHeading}><div><p className={styles.kicker}>World championship</p><h2 id="standings-board-title">{tab === "drivers" ? "Driver standings" : "Constructor standings"}</h2></div><p>Points <span>Movement</span></p></div>
          <div id={`${tab}-panel`} role="tabpanel" aria-labelledby={`${tab}-tab`} className={styles.list}>
            {tab === "drivers" ? data.drivers.map((driver) => <DriverRow driver={driver} key={driver.driver_id || driver.driver_number} />) : data.constructors.map((constructor) => <ConstructorRow constructor={constructor} key={constructor.constructor_id || constructor.team_id || constructor.team_name} />)}
            {!visible.length && <div className={styles.empty}><span aria-hidden>00</span><h3>No standings published yet</h3><p>The championship will appear here as soon as official data is available.</p></div>}
          </div>
        </section>
        <footer className={styles.footer}><span>Data supplied by {data.metadata.source}</span><span>Season {data.metadata.season}</span></footer>
      </>}
    </div>
  </main>;
}
