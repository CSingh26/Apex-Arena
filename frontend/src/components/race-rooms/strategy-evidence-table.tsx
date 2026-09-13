// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import { formatAnalysisTime, humaniseCode, type EvidenceRow } from "@/lib/strategy-frame";

import styles from "./strategy-evidence-table.module.css";

type StrategyEvidenceTableProps = {
  /** Rows already resolved and ordered by the caller's reader. */
  rows: EvidenceRow[];
  /** Evidence keys referenced by the subject but absent from the frame. */
  missingKeys?: string[];
  /** Names what the rows are evidence for, for screen reader users. */
  caption: string;
  /** Shown when the subject resolved no evidence at all. */
  emptyText: string;
};

function contextCell(evidence: EvidenceRow["evidence"]): string {
  return [
    evidence.driver_number == null ? null : `Car ${evidence.driver_number}`,
    evidence.lap_number == null ? null : `Lap ${evidence.lap_number}`,
    evidence.stint_number == null ? null : `Stint ${evidence.stint_number}`,
    `${evidence.family} · ${humaniseCode(evidence.basis).toLowerCase()}`,
  ]
    .filter((part) => part !== null)
    .join(" · ");
}

/**
 * One evidence presentation, shared by every analyst surface that has to show
 * what a published assertion actually rests on.
 */
export function StrategyEvidenceTable({
  rows,
  missingKeys = [],
  caption,
  emptyText,
}: StrategyEvidenceTableProps) {
  return (
    <details className={styles.evidence}>
      <summary>
        Evidence ({rows.length}
        {missingKeys.length ? `, ${missingKeys.length} unresolved` : ""})
      </summary>
      {rows.length ? (
        <div className={styles.tableScroll}>
          <table>
            <caption className={styles.visuallyHidden}>{caption}</caption>
            <thead>
              <tr>
                <th scope="col">Role</th>
                <th scope="col">Source</th>
                <th scope="col">Observed at</th>
                <th scope="col">Seq</th>
                <th scope="col">Context</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ key, evidence }) => (
                <tr key={key}>
                  <td>{evidence.role}</td>
                  <td>{evidence.source}</td>
                  <td>{formatAnalysisTime(evidence.observed_at)}</td>
                  <td>{evidence.sequence}</td>
                  <td>{contextCell(evidence)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className={styles.empty}>{emptyText}</p>
      )}
      {missingKeys.length ? (
        <p className={styles.warning}>
          {missingKeys.length} evidence {missingKeys.length === 1 ? "key was" : "keys were"} referenced
          but not present in the frame: {missingKeys.join(", ")}
        </p>
      ) : null}
    </details>
  );
}
