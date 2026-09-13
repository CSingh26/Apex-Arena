// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import { capabilityRows, humaniseCode } from "@/lib/strategy-frame";
import type { StrategyCapabilityAvailability, StrategyFrame } from "@/lib/types";

import styles from "./strategy-capability-grid.module.css";

type StrategyCapabilityGridProps = {
  frame: StrategyFrame;
  headingId: string;
};

const AVAILABILITY_NOTE: Record<StrategyCapabilityAvailability, string> = {
  available: "Determined from observed evidence.",
  partial: "Partly determined; the read is qualified.",
  unavailable: "Cannot be determined right now.",
  omitted: "Deliberately not produced for this view.",
};

export function StrategyCapabilityGrid({ frame, headingId }: StrategyCapabilityGridProps) {
  const rows = capabilityRows(frame);
  const blocked = rows.filter(
    (row) => row.capability.availability === "unavailable" || row.capability.availability === "omitted",
  ).length;

  return (
    <section className={styles.capabilities} aria-labelledby={headingId}>
      <header className={styles.heading}>
        <h3 id={headingId}>What the system can determine</h3>
        <small>
          {blocked === 0
            ? `All ${rows.length} checks are running`
            : `${blocked} of ${rows.length} checks cannot run`}
        </small>
      </header>
      <ul className={styles.grid} aria-labelledby={headingId}>
        {rows.map((row) => (
          <li key={row.kind} className={styles.item} data-availability={row.capability.availability}>
            <span className={styles.label}>{row.label}</span>
            <span className={styles.availability}>{row.capability.availability}</span>
            <span className={styles.reason}>
              {AVAILABILITY_NOTE[row.capability.availability]} Reason code:{" "}
              {humaniseCode(row.capability.reason)}.
            </span>
            <code className={styles.code}>{row.kind}</code>
          </li>
        ))}
      </ul>
    </section>
  );
}
