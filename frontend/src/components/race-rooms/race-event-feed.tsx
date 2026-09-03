// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import { useState } from "react";

import { describeRaceEvent, describeRecentChanges, filterRaceEvents } from "@/lib/race-intelligence";
import type { NormalizedRaceEvent, RaceEventFilter } from "@/lib/types";

import styles from "./race-event-feed.module.css";

const FILTERS: Array<{ value: RaceEventFilter; label: string }> = [
  { value: "ALL", label: "All" },
  { value: "BATTLES", label: "Battles" },
  { value: "PITS", label: "Pits" },
  { value: "RACE_CONTROL", label: "Race control" },
  { value: "MY_DRIVER", label: "My driver" },
];

type EventProps = {
  events: NormalizedRaceEvent[];
  driverName?: (driverNumber: number) => string;
};

export function RaceEventFeed({
  events,
  selectedDriver,
  driverName,
  onLoadMore,
  hasMore,
  loadingMore = false,
}: EventProps & {
  selectedDriver: number | null;
  onLoadMore: () => void;
  hasMore: boolean;
  loadingMore?: boolean;
}) {
  const [filter, setFilter] = useState<RaceEventFilter>("ALL");
  const visible = filterRaceEvents(events, filter, selectedDriver);
  return (
    <section className={styles.feed} aria-labelledby="race-events-title">
      <header>
        <div>
          <span>Structured race facts</span>
          <h2 id="race-events-title">Important events</h2>
        </div>
        <div className={styles.filters} role="group" aria-label="Filter race events">
          {FILTERS.map((item) => (
            <button
              key={item.value}
              type="button"
              aria-pressed={filter === item.value}
              disabled={item.value === "MY_DRIVER" && selectedDriver == null}
              onClick={() => setFilter(item.value)}
            >
              {item.label}
            </button>
          ))}
        </div>
      </header>
      {visible.length ? (
        <ol className={styles.events}>
          {visible.map((event) => (
            <li key={event.id}>
              <div>
                <span>{event.lap_number == null ? "SESSION" : `LAP ${event.lap_number}`}</span>
                <small>{event.event_origin === "DERIVED" ? "ApexArena insight" : "Timing fact"}</small>
              </div>
              <p>{describeRaceEvent(event, driverName)}</p>
              <b>{event.importance_level}</b>
            </li>
          ))}
        </ol>
      ) : (
        <p className={styles.empty}>No events match this view yet.</p>
      )}
      {hasMore ? (
        <button className={styles.loadMore} type="button" onClick={onLoadMore} disabled={loadingMore}>
          {loadingMore ? "Loading more events" : "Load more events"}
        </button>
      ) : null}
    </section>
  );
}

export function RecentChanges({ events, driverName }: EventProps) {
  const changes = describeRecentChanges(events, driverName, 5);
  return (
    <aside className={styles.recent} aria-labelledby="recent-changes-title">
      <span>Last meaningful updates</span>
      <h2 id="recent-changes-title">What changed?</h2>
      {changes.length ? <ul>{changes.map((change, index) => <li key={`${index}-${change}`}>{change}</li>)}</ul> : <p>No major change has been confirmed yet.</p>}
    </aside>
  );
}
