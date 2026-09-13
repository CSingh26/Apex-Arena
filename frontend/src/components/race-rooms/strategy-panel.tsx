// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import { useMemo } from "react";

import { humaniseCode } from "@/lib/strategy-frame";
import { buildFanNarrative, type FanNarrative } from "@/lib/strategy-narrative";
import type {
  BattleState,
  ControlProjection,
  IntelligenceProjection,
  IntelligenceProjectionStatusValue,
  RaceRoomMode,
  StrategyFrame,
} from "@/lib/types";

import { AnalystStrategyFrame } from "./analyst-strategy-frame";
import { FanStrategyBriefing } from "./fan-strategy-briefing";
import styles from "./strategy-panel.module.css";

const PANEL_TITLE_ID = "strategy-panel-title";
const EMPTY_BATTLES: readonly BattleState[] = [];

type StrategyPanelProps = {
  mode: RaceRoomMode;
  frame: StrategyFrame | null | undefined;
  projection: IntelligenceProjection | null | undefined;
  control: ControlProjection | null | undefined;
  battles?: readonly BattleState[];
  driverName?: (driverNumber: number) => string;
};

/**
 * Why strategy analysis is missing, in the user's terms. Every branch says
 * something true about the pipeline; none of them leaves a blank card.
 */
const PROJECTION_EXPLANATIONS: Record<IntelligenceProjectionStatusValue, string> = {
  unknown:
    "The strategy pipeline has not reported a status for this session yet, "
    + "so no strategy reads are shown.",
  current:
    "The strategy pipeline is up to date, but it has not published a strategy frame "
    + "for this view.",
  replay:
    "This replay position is covered by the pipeline, but no strategy frame was published for it.",
  pending:
    "Strategy analysis is still being computed for this point in the session. "
    + "Reads will appear once the pipeline catches up.",
  stale:
    "This view is ahead of the completed analysis. Strategy reads are held back "
    + "rather than guessed at a cursor the pipeline has not reached.",
  historical_effects_unverified:
    "Historical effects for this session have not been verified, so strategy reads "
    + "are withheld rather than presented as confirmed.",
  unavailable: "Strategy analysis is unavailable for this session.",
};

function defaultDriverName(driverNumber: number): string {
  return `Car ${driverNumber}`;
}

function StrategyUnavailableNotice({
  frame,
  projection,
  control,
}: {
  frame: StrategyFrame | null | undefined;
  projection: IntelligenceProjection | null | undefined;
  control: ControlProjection | null | undefined;
}) {
  const status = projection?.status ?? "unknown";
  const reasons: string[] = [];
  if (frame) {
    for (const limitation of frame.limitations) {
      reasons.push(humaniseCode(limitation));
    }
  }
  if (projection?.failure_code) {
    reasons.push(`Pipeline failure: ${humaniseCode(projection.failure_code)}`);
  }
  if (projection?.historical_effects_unverified) {
    reasons.push("Historical effects have not been verified for this session");
  }
  if (control?.history_truncated === true) {
    reasons.push("Race control history was truncated for this session");
  }

  return (
    <div className={styles.notice} role="note">
      <p className={styles.noticeLead}>
        Strategy analysis is not available for this view yet.
      </p>
      <p className={styles.noticeBody}>{PROJECTION_EXPLANATIONS[status]}</p>
      {reasons.length ? (
        <>
          <h3 className={styles.noticeSubtitle}>What the backend reported</h3>
          <ul className={styles.noticeReasons}>
            {reasons.map((reason) => (
              <li key={reason}>{reason}.</li>
            ))}
          </ul>
        </>
      ) : null}
      <p className={styles.noticeFooter}>
        Nothing is inferred in place of the missing analysis — the rest of the room keeps showing
        observed timing and race control facts.
      </p>
    </div>
  );
}

function panelTitle(mode: RaceRoomMode): string {
  return mode === "FAN" ? "The strategy story" : "Strategy frame";
}

function panelSubtitle(mode: RaceRoomMode, available: boolean): string {
  if (!available) return "Analysis unavailable";
  return mode === "FAN"
    ? "Plain language · hedged where the data is"
    : "Full deterministic frame · evidence and limits";
}

export function StrategyPanel({
  mode,
  frame,
  projection,
  control,
  battles = EMPTY_BATTLES,
  driverName = defaultDriverName,
}: StrategyPanelProps) {
  const available = frame != null && frame.projection_status !== "unavailable";
  const publishedFrame = available ? frame : null;

  const narrative = useMemo(
    () => buildFanNarrative({ frame: publishedFrame, control, battles, driverName }),
    [publishedFrame, control, battles, driverName],
  );

  return (
    <section
      className={styles.panel}
      aria-labelledby={PANEL_TITLE_ID}
      data-mode={mode.toLowerCase()}
    >
      <header className={styles.heading}>
        <div>
          <span>Strategy intelligence</span>
          <h2 id={PANEL_TITLE_ID}>{panelTitle(mode)}</h2>
        </div>
        <small>{panelSubtitle(mode, available)}</small>
      </header>
      <StrategyBody
        mode={mode}
        frame={publishedFrame}
        sourceFrame={frame}
        projection={projection}
        control={control}
        narrative={narrative}
        driverName={driverName}
      />
    </section>
  );
}

function StrategyBody({
  mode,
  frame,
  sourceFrame,
  projection,
  control,
  narrative,
  driverName,
}: {
  mode: RaceRoomMode;
  /** The frame only when the projection actually published one. */
  frame: StrategyFrame | null;
  /** Whatever the backend sent, so the notice can quote its limitations. */
  sourceFrame: StrategyFrame | null | undefined;
  projection: IntelligenceProjection | null | undefined;
  control: ControlProjection | null | undefined;
  narrative: FanNarrative;
  driverName: (driverNumber: number) => string;
}) {
  if (!frame) {
    return (
      <StrategyUnavailableNotice frame={sourceFrame} projection={projection} control={control} />
    );
  }
  if (mode === "FAN") {
    return <FanStrategyBriefing narrative={narrative} />;
  }
  return (
    <AnalystStrategyFrame
      frame={frame}
      projection={projection}
      control={control}
      driverName={driverName}
    />
  );
}
