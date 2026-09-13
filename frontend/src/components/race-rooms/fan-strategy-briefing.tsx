// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import type { FanNarrative, FanStatement } from "@/lib/strategy-narrative";

import styles from "./fan-strategy-briefing.module.css";

type FanStrategyBriefingProps = {
  narrative: FanNarrative;
};

const TONE_BADGES: Record<FanStatement["tone"], string | null> = {
  observation: null,
  update: "Revised",
  revision: "No longer holds",
};

function StatementList({
  statements,
  emptyText,
  labelledBy,
}: {
  statements: FanStatement[];
  emptyText: string;
  labelledBy: string;
}) {
  if (!statements.length) {
    return <p className={styles.empty}>{emptyText}</p>;
  }
  return (
    <ul className={styles.statements} aria-labelledby={labelledBy}>
      {statements.map((statement) => (
        <li key={statement.id} className={styles.statement} data-tone={statement.tone}>
          <p>{statement.text}</p>
          <Badges statement={statement} />
        </li>
      ))}
    </ul>
  );
}

function Badges({ statement }: { statement: FanStatement }) {
  const toneBadge = TONE_BADGES[statement.tone];
  if (!toneBadge && !statement.hedged) return null;
  return (
    <p className={styles.badges}>
      {toneBadge ? <span className={styles.toneBadge}>{toneBadge}</span> : null}
      {statement.hedged ? (
        <span className={styles.hedgeBadge} title="The evidence behind this read is incomplete">
          Not confirmed
        </span>
      ) : null}
    </p>
  );
}

function Question({
  id,
  question,
  statements,
  emptyText,
}: {
  id: string;
  question: string;
  statements: FanStatement[];
  emptyText: string;
}) {
  return (
    <section className={styles.question} aria-labelledby={id}>
      <h3 id={id}>{question}</h3>
      <StatementList statements={statements} emptyText={emptyText} labelledBy={id} />
    </section>
  );
}

export function FanStrategyBriefing({ narrative }: FanStrategyBriefingProps) {
  return (
    <div className={styles.briefing}>
      <Question
        id="fan-strategy-happening"
        question="What is happening?"
        statements={narrative.happening}
        emptyText={
          "Nothing in the strategy picture has moved yet. "
          + "The session is running without an open strategy question."
        }
      />
      <Question
        id="fan-strategy-matters"
        question="Why does it matter?"
        statements={narrative.matters}
        emptyText="There is no open strategy call to weigh up right now."
      />
      <Question
        id="fan-strategy-watch"
        question="What to watch next?"
        statements={narrative.watch}
        emptyText={
          "Nothing specific is building. The next pit stop or weather shift will change that."
        }
      />
      {narrative.caveats.length ? (
        <section className={styles.caveats} aria-labelledby="fan-strategy-caveats">
          <h3 id="fan-strategy-caveats">What we cannot be sure of</h3>
          <ul aria-labelledby="fan-strategy-caveats">
            {narrative.caveats.map((caveat) => (
              <li key={caveat}>{caveat}</li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
