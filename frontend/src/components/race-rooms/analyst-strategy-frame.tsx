// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import { formatAnalysisTime, humaniseCode, rankSituations } from "@/lib/strategy-frame";
import type { ControlProjection, IntelligenceProjection, StrategyFrame } from "@/lib/types";

import { StrategyCapabilityGrid } from "./strategy-capability-grid";
import { StrategySituationCard } from "./strategy-situation-card";
import styles from "./analyst-strategy-frame.module.css";

type AnalystStrategyFrameProps = {
  frame: StrategyFrame;
  projection: IntelligenceProjection | null | undefined;
  control: ControlProjection | null | undefined;
  driverName: (driverNumber: number) => string;
};

function FrameIdentity({
  frame,
  projection,
}: {
  frame: StrategyFrame;
  projection: IntelligenceProjection | null | undefined;
}) {
  return (
    <section className={styles.identitySection} aria-labelledby="analyst-strategy-identity">
      <h3 id="analyst-strategy-identity" className={styles.visuallyHidden}>
        Frame identity
      </h3>
      <dl className={styles.identity}>
        <div>
          <dt>Analysis time</dt>
          <dd>{formatAnalysisTime(frame.analysis_time)}</dd>
        </div>
        <div>
          <dt>Frame sequence</dt>
          <dd>
            {frame.sequence} <span>(history {frame.history_sequence})</span>
          </dd>
        </div>
        <div>
          <dt>Clock basis</dt>
          <dd>{humaniseCode(frame.clock_basis)}</dd>
        </div>
        <div>
          <dt>Projection</dt>
          <dd>
            {humaniseCode(frame.projection_status)}
            {projection ? <span>(pipeline: {humaniseCode(projection.status)})</span> : null}
          </dd>
        </div>
        <div>
          <dt>Algorithm</dt>
          <dd>{projection?.algorithm_version ?? "Not reported"}</dd>
        </div>
        <div>
          <dt>Semantic identity</dt>
          <dd className={styles.wrap}>{frame.semantic_identity}</dd>
        </div>
      </dl>
    </section>
  );
}

function DataQuality({
  frame,
  control,
}: {
  frame: StrategyFrame;
  control: ControlProjection | null | undefined;
}) {
  const notes: string[] = [];
  if (frame.situations_truncated) {
    notes.push(
      `Situations were truncated to the frame budget; ${frame.omitted_situations} were omitted from this frame.`,
    );
  } else if (frame.omitted_situations > 0) {
    notes.push(`${frame.omitted_situations} situations were omitted from this frame.`);
  }
  if (frame.suppressed_events > 0) {
    notes.push(
      `${frame.suppressed_events} source events were suppressed before analysis.`,
    );
  }
  for (const limitation of frame.limitations) {
    notes.push(`Frame limitation: ${humaniseCode(limitation)}.`);
  }
  if (control?.history_truncated === true) {
    notes.push(
      "Control history is truncated, so clean-lap inference since a pit stop is uncertain for this session.",
    );
  }
  if (control) {
    notes.push(
      `Control cursor at sequence ${control.sequence}: `
        + `lifecycle ${control.lifecycle.value}, `
        + `neutralization ${control.neutralization.value}, `
        + `flag ${control.track_flag.value}, `
        + `DRS ${control.drs_permission.value}.`,
    );
  } else {
    notes.push("No race control projection is attached to this view.");
  }

  return (
    <section className={styles.quality} aria-labelledby="analyst-strategy-quality">
      <h3 id="analyst-strategy-quality">Data quality</h3>
      <ul aria-labelledby="analyst-strategy-quality">
        {notes.map((note) => (
          <li key={note}>{note}</li>
        ))}
      </ul>
    </section>
  );
}

export function AnalystStrategyFrame({
  frame,
  projection,
  control,
  driverName,
}: AnalystStrategyFrameProps) {
  const situations = rankSituations(frame.situations);
  const evidenceCount = Object.keys(frame.evidence ?? {}).length;

  return (
    <div className={styles.analyst}>
      <FrameIdentity frame={frame} projection={projection} />
      <StrategyCapabilityGrid frame={frame} headingId="analyst-strategy-capabilities" />

      <section className={styles.situations} aria-labelledby="analyst-strategy-situations">
        <header className={styles.heading}>
          <h3 id="analyst-strategy-situations">Situations</h3>
          <small>
            {situations.length} in frame · {evidenceCount} evidence{" "}
            {evidenceCount === 1 ? "row" : "rows"}
          </small>
        </header>
        {situations.length ? (
          <div className={styles.cards}>
            {situations.map((situation) => (
              <StrategySituationCard
                key={situation.revision_id}
                frame={frame}
                situation={situation}
                driverName={driverName}
              />
            ))}
          </div>
        ) : (
          <p className={styles.empty}>
            The frame is published and current, but no strategy situation is open at this cursor.
            The capability list above shows which checks are running.
          </p>
        )}
      </section>

      <DataQuality frame={frame} control={control} />
    </div>
  );
}
