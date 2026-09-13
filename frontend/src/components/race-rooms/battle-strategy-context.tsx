// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import {
  battleContextRows,
  battleEvidenceRows,
  battleFanCaveat,
  battleHighlights,
  battleUndeterminedLabels,
  prominenceRows,
} from "@/lib/battle-context";
import { humaniseCode } from "@/lib/strategy-frame";
import type { StrategyBattleContext } from "@/lib/types";

import { StrategyEvidenceTable } from "./strategy-evidence-table";
import styles from "./battle-strategy-context.module.css";

type BattleContextProps = {
  context: StrategyBattleContext;
  leadDriverNumber: number;
  chasingDriverNumber: number;
  driverName: (driverNumber: number) => string;
};

function RowList({ rows }: { rows: { label: string; value: string }[] }) {
  if (!rows.length) return null;
  return (
    <dl className={styles.rows}>
      {rows.map((row) => (
        <div key={row.label}>
          <dt>{row.label}</dt>
          <dd>{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function CodeList({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div className={styles.codeList}>
      <h5>{title}</h5>
      <ul>
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Fan mode: why this battle is worth watching, in plain words.
 *
 * Only determined values reach the list, and the closing caveat names what the
 * engine genuinely cannot decide rather than letting silence imply a negative.
 */
export function FanBattleContext({
  context,
  leadDriverNumber,
  chasingDriverNumber,
  driverName,
}: BattleContextProps) {
  const highlights = battleHighlights({
    context,
    leadDriverNumber,
    chasingDriverNumber,
    driverName,
  });
  const caveat = battleFanCaveat(context);
  if (!highlights.length && !caveat) return null;
  return (
    <div className={styles.fan}>
      {highlights.length ? (
        <ul className={styles.highlights} aria-label="Why this battle matters">
          {highlights.map((highlight) => (
            <li key={highlight.id}>{highlight.text}</li>
          ))}
        </ul>
      ) : null}
      {caveat ? <p className={styles.caveat}>{caveat}</p> : null}
    </div>
  );
}

/**
 * Analyst mode: the full published context, including what the engine ranked
 * this battle on, the evidence behind it and everything it could not decide.
 */
export function AnalystBattleContext({
  context,
  leadDriverNumber,
  chasingDriverNumber,
  driverName,
}: BattleContextProps) {
  const rows = battleContextRows(context);
  const undetermined = battleUndeterminedLabels(context);
  const evidence = battleEvidenceRows(context);
  const battleLabel = `${driverName(leadDriverNumber)} versus ${driverName(chasingDriverNumber)}`;

  return (
    <div className={styles.analyst}>
      <section aria-label={`Battle context for ${battleLabel}`}>
        <h5 className={styles.sectionTitle}>Measured context</h5>
        {rows.length ? (
          <RowList rows={rows} />
        ) : (
          <p className={styles.empty}>
            The engine published this context with no measured values attached.
          </p>
        )}
      </section>

      <section aria-label={`Prominence for ${battleLabel}`}>
        <h5 className={styles.sectionTitle}>
          Prominence <span>score {context.prominence.score}</span>
        </h5>
        <RowList rows={prominenceRows(context.prominence)} />
      </section>

      <CodeList title="Not determined by this system" items={undetermined} />
      <CodeList
        title="Limitations"
        items={context.limitations.map((limitation) => humaniseCode(limitation))}
      />

      <StrategyEvidenceTable
        rows={evidence}
        caption={`Battle evidence rows for ${battleLabel}`}
        emptyText="This battle context carries no evidence rows."
      />
    </div>
  );
}
