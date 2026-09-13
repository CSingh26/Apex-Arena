// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import {
  evidenceFor,
  formatAnalysisTime,
  humaniseCode,
  payloadRows,
  situationLabel,
  undeterminedLabels,
} from "@/lib/strategy-frame";
import type { StrategyFrame, StrategySituation } from "@/lib/types";

import { StrategyEvidenceTable } from "./strategy-evidence-table";
import styles from "./strategy-situation-card.module.css";

type StrategySituationCardProps = {
  frame: StrategyFrame;
  situation: StrategySituation;
  driverName: (driverNumber: number) => string;
};

function CodeList({ title, codes }: { title: string; codes: string[] }) {
  if (!codes.length) return null;
  return (
    <div className={styles.codeList}>
      <h5>{title}</h5>
      <ul>
        {codes.map((code) => (
          <li key={code}>{humaniseCode(code)}</li>
        ))}
      </ul>
    </div>
  );
}

function PayloadTable({ situation }: { situation: StrategySituation }) {
  const rows = payloadRows(situation.payload);
  if (!rows.length) {
    return (
      <p className={styles.emptyPayload}>
        This situation carries no measured values — only the evidence that opened it.
      </p>
    );
  }
  return (
    <dl className={styles.payload}>
      {rows.map((row) => (
        <div key={row.label}>
          <dt>{row.label}</dt>
          <dd>{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function EvidenceTable({
  frame,
  situation,
}: {
  frame: StrategyFrame;
  situation: StrategySituation;
}) {
  const { rows, missingKeys } = evidenceFor(frame, situation);
  return (
    <StrategyEvidenceTable
      rows={rows}
      missingKeys={missingKeys}
      caption={`Evidence rows for ${situationLabel(situation)}`}
      emptyText="No evidence row resolved for this situation in the published frame."
    />
  );
}

export function StrategySituationCard({
  frame,
  situation,
  driverName,
}: StrategySituationCardProps) {
  const undetermined = undeterminedLabels(situation.payload);
  const withdrawn = situation.status === "withdrawn" || situation.transition === "withdrawn";
  const label = situationLabel(situation);

  return (
    <article
      className={styles.card}
      data-status={situation.status}
      data-availability={situation.availability}
      aria-label={`${label}${withdrawn ? ", withdrawn" : ""}`}
    >
      <header className={styles.header}>
        <div>
          <h4>{label}</h4>
          <code>{situation.kind}</code>
        </div>
        <p className={styles.badges}>
          <span className={styles.status}>{situation.status}</span>
          <span className={styles.transition}>{situation.transition}</span>
          <span className={styles.availability}>{situation.availability}</span>
          <span className={styles.confidence}>{situation.observation_confidence}</span>
        </p>
      </header>

      <p className={styles.participants}>
        {situation.participants.length
          ? situation.participants.map((driver) => driverName(driver)).join(" vs ")
          : "No driver participants recorded"}
      </p>

      {withdrawn ? (
        <p className={styles.withdrawn}>
          Withdrawn at sequence {situation.sequence}
          {situation.superseded_revision_id
            ? `; superseded revision ${situation.superseded_revision_id}`
            : ""}
          .
        </p>
      ) : null}

      <PayloadTable situation={situation} />

      {undetermined.length ? (
        <div className={styles.codeList}>
          <h5>Not determined by this system</h5>
          <ul>
            {undetermined.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <CodeList title="Assumptions" codes={situation.assumptions} />
      <CodeList title="Limitations" codes={situation.limitations} />

      <EvidenceTable frame={frame} situation={situation} />

      <dl className={styles.identity}>
        <div>
          <dt>Analysis time</dt>
          <dd>{formatAnalysisTime(situation.analysis_time)}</dd>
        </div>
        <div>
          <dt>Sequence / history</dt>
          <dd>
            {situation.sequence} / {situation.history_sequence}
          </dd>
        </div>
        <div>
          <dt>Source sequence</dt>
          <dd>{situation.source_sequence}</dd>
        </div>
        <div>
          <dt>Revision</dt>
          <dd>{situation.revision_id}</dd>
        </div>
      </dl>
    </article>
  );
}
