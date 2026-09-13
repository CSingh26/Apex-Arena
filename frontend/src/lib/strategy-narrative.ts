// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Deterministic plain-language narrative for Fan Mode.
 *
 * Three rules hold everywhere in this file:
 *  1. Every sentence traces to a published situation, battle or control
 *     observation. Nothing is inferred beyond what the frame asserts.
 *  2. A qualified or partially-available situation is hedged in the wording
 *     itself, not only in a badge.
 *  3. A withdrawn situation is told as a revision, never dropped.
 */
import { humaniseCode, isHedged, rankSituations } from "@/lib/strategy-frame";
import { formatDuration, formatPitStop } from "@/lib/timing";
import type {
  BattleState,
  ControlProjection,
  StrategyFrame,
  StrategySituation,
} from "@/lib/types";

export type FanStatementTone = "observation" | "revision" | "update";

export type FanStatement = {
  id: string;
  text: string;
  hedged: boolean;
  tone: FanStatementTone;
};

export type FanNarrative = {
  happening: FanStatement[];
  matters: FanStatement[];
  watch: FanStatement[];
  caveats: string[];
};

export type FanNarrativeInput = {
  frame: StrategyFrame | null | undefined;
  control?: ControlProjection | null;
  battles?: readonly BattleState[];
  driverName?: (driverNumber: number) => string;
};

/** How many strategy reads Fan Mode will narrate before deferring to Analyst Mode. */
export const FAN_SITUATION_LIMIT = 5;

const QUIET_CONTROL_VALUES = new Set(["", "none", "clear", "unknown", "green", "all_clear"]);

function defaultDriverName(driverNumber: number): string {
  return `Car ${driverNumber}`;
}

/** Pick between a plain and a qualified verb phrase so the prose itself hedges. */
function modal(hedged: boolean, plain: string, qualified: string): string {
  return hedged ? qualified : plain;
}

function seconds(value: number): string {
  const rendered = formatDuration(Math.abs(value), 3600);
  return rendered === "—" ? "a margin the data does not pin down" : `${rendered}s`;
}

type SituationCopy = { happening: string; matters: string; watch: string | null };

function participantNames(
  situation: StrategySituation,
  driverName: (driverNumber: number) => string,
): { first: string; second: string | null } {
  const [first, second] = situation.participants;
  return {
    first: first == null ? "This car" : driverName(first),
    second: second == null ? null : driverName(second),
  };
}

function stintDivergenceCopy(
  situation: StrategySituation,
  first: string,
  second: string | null,
  hedged: boolean,
): SituationCopy {
  const rival = second ?? "the car it is racing";
  const offset = situation.payload.age_offset_laps;
  const offsetLaps = Math.abs(Math.round(offset ?? 0));
  const age = offset == null || offset === 0
    ? ""
    : ` ${first}'s tyres are ${offsetLaps} ${offsetLaps === 1 ? "lap" : "laps"} `
      + `${offset > 0 ? "older" : "fresher"}.`;
  return {
    happening: `${first} and ${rival} `
      + `${modal(hedged, "are running", "look to be running")} different tyre plans.${age}`,
    matters:
      "When two cars are on different plans, the fight between them gets settled "
      + "in the pit lane as much as on track.",
    watch: `Watch which of them stops first — that choice usually decides the order.`,
  };
}

function relativePaceCopy(
  situation: StrategySituation,
  first: string,
  second: string | null,
  hedged: boolean,
): SituationCopy {
  const rival = second ?? "the car it is racing";
  const pace = situation.payload.pace;
  const difference = pace ? pace.difference_seconds : null;
  const quicker = difference == null || difference <= 0;
  const margin = difference == null ? "" : ` The edge is about ${seconds(difference)} a lap.`;
  const plain = quicker ? "is lapping quicker than" : "is lapping slower than";
  const qualified = quicker
    ? "appears to be lapping quicker than"
    : "appears to be lapping slower than";
  return {
    happening: `${first} ${modal(hedged, plain, qualified)} ${rival} right now.${margin}`,
    matters:
      "A pace edge compounds. Held for a run of laps it turns a gap into a passing chance, "
      + "or lets the car in front disappear.",
    watch: `Watch the gap between them settle or shrink over the next few laps.`,
  };
}

function pitWindowCopy(
  situation: StrategySituation,
  first: string,
  hedged: boolean,
): SituationCopy {
  const loss = situation.payload.pit_window?.loss_seconds ?? null;
  const cost = loss == null
    ? "A stop costs real time in the pit lane, so it only pays off if fresh tyres win that time back."
    : `A stop costs roughly ${formatPitStop(loss)} in the pit lane, so it only pays off if `
      + "fresh tyres win that time back.";
  return {
    happening: `${first} ${modal(
      hedged,
      "is into the window where stopping makes sense",
      "may be into the window where stopping makes sense",
    )}.`,
    matters: cost,
    watch: `Watch for ${first} to peel into the pit lane.`,
  };
}

function undercutCopy(
  situation: StrategySituation,
  first: string,
  second: string | null,
  hedged: boolean,
): SituationCopy {
  const rival = second ?? "the car ahead";
  const required = situation.payload.required_gain_seconds;
  const matters = required == null
    ? `An undercut only works if the fresh tyres pay off before ${rival} answers with a stop of their own.`
    : `To make it work ${first} needs to find about ${seconds(required)} before ${rival} `
      + "answers with a stop of their own.";
  return {
    happening: `${first} ${modal(
      hedged,
      "is in position to try an undercut on",
      "may be in position to try an undercut on",
    )} ${rival} by stopping first.`,
    matters,
    watch: `Watch whether ${rival} covers the stop on the very next lap.`,
  };
}

function overcutCopy(
  situation: StrategySituation,
  first: string,
  second: string | null,
  hedged: boolean,
): SituationCopy {
  const rival = second ?? "the car ahead";
  const required = situation.payload.required_gain_seconds;
  const matters = required == null
    ? `An overcut asks ${first} to stay quick on old tyres while ${rival} is in the pit lane.`
    : `${first} has to find about ${seconds(required)} on worn tyres while ${rival} is in the pit lane.`;
  return {
    happening: `${first} ${modal(
      hedged,
      "is in position to try an overcut on",
      "may be in position to try an overcut on",
    )} ${rival} by staying out longer.`,
    matters,
    watch: `Watch ${first}'s next couple of laps — they have to be clean ones.`,
  };
}

function neutralizedCopy(
  situation: StrategySituation,
  first: string,
  hedged: boolean,
): SituationCopy {
  const neutralization = situation.payload.neutralization
    ? humaniseCode(situation.payload.neutralization).toLowerCase()
    : "a neutralised track";
  return {
    happening: `${first} ${modal(
      hedged,
      "stopped while the track was under",
      "appears to have stopped while the track was under",
    )} ${neutralization}.`,
    matters:
      "Stopping while the whole field is slowed costs far less time than a normal stop, "
      + "so the order afterwards can look very different.",
    watch: `Watch where ${first} rejoins once the track goes green again.`,
  };
}

function extraStopCopy(
  situation: StrategySituation,
  first: string,
  hedged: boolean,
): SituationCopy {
  const range = situation.payload.required_average_gain_seconds;
  const laps = situation.payload.remaining_laps;
  const over = laps == null
    ? "for the rest of the running"
    : `over the remaining ${laps} ${laps === 1 ? "lap" : "laps"}`;
  const matters = range
    ? `It would only pay off if ${first} could find roughly ${seconds(range[0])} a lap ${over}.`
    : `An extra stop hands back track position that has to be won again on track.`;
  return {
    happening: `An extra stop for ${first} ${modal(hedged, "is on the table", "may be on the table")}.`,
    matters,
    watch: `Watch whether ${first} commits to that extra stop or tries to nurse the tyres home.`,
  };
}

function weatherCopy(situation: StrategySituation, hedged: boolean): SituationCopy {
  const { rainfall_before: before, rainfall_now: now, track_temperature_change: track } = situation.payload;
  if (before === false && now === true) {
    return {
      happening: modal(hedged, `Rain has started falling.`, `Rain appears to have started falling.`),
      matters: `A wet track changes grip, tyre choice and how hard staying out is to justify.`,
      watch: `Watch for the first cars to switch to wet-weather tyres.`,
    };
  }
  if (before === true && now === false) {
    return {
      happening: modal(hedged, `The rain has stopped.`, `The rain appears to have stopped.`),
      matters: `A drying track rewards whoever is brave enough to switch back to slicks first.`,
      watch: `Watch for the first driver to gamble on dry tyres.`,
    };
  }
  if (now === true) {
    return {
      happening: modal(hedged, `Rain is still falling.`, `Rain appears to still be falling.`),
      matters: `The longer it stays wet, the harder a dry-tyre gamble is to justify.`,
      watch: `Watch whether the rain eases before the leaders need to stop again.`,
    };
  }
  if (track != null && track !== 0) {
    return {
      happening: modal(
        hedged,
        `The track surface is ${track > 0 ? "warming up" : "cooling down"}.`,
        `The track surface looks to be ${track > 0 ? "warming up" : "cooling down"}.`,
      ),
      matters: `Temperature swings change how quickly tyres come alive and how long they last.`,
      watch: `Watch whether lap times start to drop off sooner than expected.`,
    };
  }
  return {
    happening: modal(
      hedged,
      "Conditions around the circuit have changed.",
      "Conditions around the circuit appear to have changed.",
    ),
    matters: `Shifting conditions move the goalposts for every tyre decision still to come.`,
    watch: `Watch the next few laps for signs that the change is sticking.`,
  };
}

function situationCopy(
  situation: StrategySituation,
  driverName: (driverNumber: number) => string,
): SituationCopy {
  const hedged = isHedged(situation);
  const { first, second } = participantNames(situation, driverName);
  switch (situation.kind) {
    case "stint_divergence":
      return stintDivergenceCopy(situation, first, second, hedged);
    case "relative_pace":
      return relativePaceCopy(situation, first, second, hedged);
    case "pit_window":
      return pitWindowCopy(situation, first, hedged);
    case "undercut_condition":
      return undercutCopy(situation, first, second, hedged);
    case "overcut_condition":
      return overcutCopy(situation, first, second, hedged);
    case "neutralized_pit_context":
      return neutralizedCopy(situation, first, hedged);
    case "extra_stop_consequence":
      return extraStopCopy(situation, first, hedged);
    case "weather_change":
      return weatherCopy(situation, hedged);
  }
}

function withdrawalReason(situation: StrategySituation): string {
  const [limitation] = situation.limitations;
  if (limitation) return ` because ${humaniseCode(limitation).toLowerCase()}`;
  if (situation.superseded_revision_id) return ` — a newer reading replaced it`;
  return ` — the evidence behind it no longer stands`;
}

function toneFor(situation: StrategySituation): FanStatementTone {
  if (situation.status === "withdrawn" || situation.transition === "withdrawn") return "revision";
  if (situation.transition === "revised") return "update";
  return "observation";
}

function happeningText(situation: StrategySituation, copy: SituationCopy): string {
  const tone = toneFor(situation);
  if (tone === "revision") {
    return `Earlier read: ${copy.happening} That no longer holds${withdrawalReason(situation)}.`;
  }
  if (tone === "update") return `Updated read: ${copy.happening}`;
  return copy.happening;
}

function controlValue(observation: { value: string } | undefined): string | null {
  const value = observation?.value?.trim().toLowerCase();
  if (!value || QUIET_CONTROL_VALUES.has(value)) return null;
  return humaniseCode(value).toLowerCase();
}

function controlStatements(control: ControlProjection | null | undefined): {
  happening: FanStatement[];
  watch: FanStatement[];
} {
  const happening: FanStatement[] = [];
  const watch: FanStatement[] = [];
  if (!control) return { happening, watch };

  const neutralization = controlValue(control.neutralization);
  if (neutralization) {
    happening.push({
      id: "control-neutralization",
      text: `The track is under ${neutralization} right now, so the field is slowed.`,
      hedged: false,
      tone: "observation",
    });
    watch.push({
      id: "control-neutralization-watch",
      text: "Watch the pit lane — stopping while the field is slowed is much cheaper than usual.",
      hedged: false,
      tone: "observation",
    });
    return { happening, watch };
  }

  const flag = controlValue(control.track_flag);
  if (flag) {
    happening.push({
      id: "control-flag",
      text: `Race control is showing ${flag}.`,
      hedged: false,
      tone: "observation",
    });
  }
  return { happening, watch };
}

function battleStatements(
  battles: readonly BattleState[],
  driverName: (driverNumber: number) => string,
): FanStatement[] {
  return [...battles]
    .filter((battle) => battle.status !== "RESOLVED" && Number.isFinite(battle.interval_seconds))
    .sort((left, right) => left.interval_seconds - right.interval_seconds)
    .slice(0, 2)
    .map((battle) => {
      const chaser = driverName(battle.chasing_driver_number);
      const leader = driverName(battle.lead_driver_number);
      const margin = seconds(battle.interval_seconds);
      return {
        id: `battle-${battle.id}`,
        text: `${chaser} is ${margin} behind ${leader} `
          + `in the fight for P${battle.lead_position} — watch for a move.`,
        hedged: false,
        tone: "observation" as const,
      };
    });
}

function frameCaveats(frame: StrategyFrame, narrated: number): string[] {
  const caveats: string[] = [];
  const total = frame.situations.length;
  const remaining = total - narrated;
  if (remaining > 0) {
    caveats.push(
      `${total - narrated} further strategy ${remaining === 1 ? "read is" : "reads are"} `
        + "listed in Analyst mode.",
    );
  }
  if (frame.situations_truncated || frame.omitted_situations > 0) {
    caveats.push(
      "The session produced more strategy reads than this frame can carry; "
        + `${frame.omitted_situations} were left out entirely.`,
    );
  }
  if (frame.suppressed_events > 0) {
    caveats.push(
      `${frame.suppressed_events} source `
        + `${frame.suppressed_events === 1 ? "event was" : "events were"} `
        + "suppressed before this analysis ran.",
    );
  }
  for (const limitation of frame.limitations) {
    caveats.push(`${humaniseCode(limitation)}.`);
  }
  const blocked = Object.values(frame.capabilities ?? {}).filter(
    (capability) => capability.availability === "unavailable" || capability.availability === "omitted",
  ).length;
  if (blocked > 0) {
    caveats.push(
      `${blocked} of the 8 strategy checks cannot run on the data available — `
        + "Analyst mode lists which, and why.",
    );
  }
  return caveats;
}

export function buildFanNarrative({
  frame,
  control,
  battles = [],
  driverName = defaultDriverName,
}: FanNarrativeInput): FanNarrative {
  const situationHappening: FanStatement[] = [];
  const matters: FanStatement[] = [];
  const watch: FanStatement[] = [];

  const situations = frame ? rankSituations(frame.situations).slice(0, FAN_SITUATION_LIMIT) : [];
  for (const situation of situations) {
    const copy = situationCopy(situation, driverName);
    const hedged = isHedged(situation);
    const tone = toneFor(situation);
    situationHappening.push({
      id: `${situation.revision_id}-happening`,
      text: happeningText(situation, copy),
      hedged,
      tone,
    });
    if (tone !== "revision") {
      matters.push({ id: `${situation.revision_id}-matters`, text: copy.matters, hedged, tone });
      if (copy.watch) {
        watch.push({ id: `${situation.revision_id}-watch`, text: copy.watch, hedged, tone });
      }
    }
  }

  const fromControl = controlStatements(control);
  // Deduplicated before counting so the "further reads" caveat reflects what a
  // reader can actually see, not what was sliced off.
  const narratedSituations = dedupe(situationHappening);
  watch.push(...fromControl.watch);
  watch.push(...battleStatements(battles, driverName));

  const caveats = frame ? frameCaveats(frame, narratedSituations.length) : [];
  if (control?.history_truncated === true) {
    caveats.push(
      "Race control history was truncated, so how many clean laps a car has had since its stop is uncertain.",
    );
  }

  return {
    happening: dedupe([...fromControl.happening, ...narratedSituations]),
    matters: dedupe(matters).slice(0, 4),
    watch: dedupe(watch).slice(0, 4),
    caveats,
  };
}

function dedupe(statements: FanStatement[]): FanStatement[] {
  const seen = new Set<string>();
  return statements.filter((statement) => {
    if (seen.has(statement.text)) return false;
    seen.add(statement.text);
    return true;
  });
}
