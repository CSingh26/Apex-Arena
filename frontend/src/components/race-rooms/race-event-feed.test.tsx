// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RaceEventFeed, RecentChanges } from "./race-event-feed";
import type { NormalizedRaceEvent } from "@/lib/types";

function event(id: string, sequence: number, type: string, drivers: number[] = []): NormalizedRaceEvent {
  return {
    id, session_key: "11334", source: "apexarena", raw_event_id: null,
    event_time: "2026-07-19T13:00:00Z", received_at: "2026-07-19T13:00:00Z",
    processed_at: "2026-07-19T13:00:00Z", sequence_number: sequence, event_type: type,
    event_origin: "DERIVED", driver_numbers: drivers, primary_driver_number: drivers[0] ?? null,
    secondary_driver_number: drivers[1] ?? null, position_before: null, position_after: null,
    gap_seconds: null, interval_seconds: null, lap_number: sequence, importance: 0.8,
    importance_level: "IMPORTANT", confidence: 0.9, confidence_level: "HIGH",
    derivation: null, payload: {}, dedup_key: id, is_replay: true,
  };
}

const events = [
  event("battle", 20, "BATTLE_STARTED", [4, 16]),
  event("pit", 21, "PIT_ENTRY", [44]),
  event("flag", 22, "RED_FLAG"),
];

describe("RaceEventFeed", () => {
  it("filters the structured feed and preserves lap ordering", async () => {
    render(<RaceEventFeed events={events} selectedDriver={4} driverName={(number) => `Driver ${number}`} onLoadMore={vi.fn()} hasMore />);
    expect(screen.getByText("LAP 20")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Pits" }));
    expect(screen.getByText("LAP 21")).toBeVisible();
    expect(screen.queryByText("LAP 20")).not.toBeInTheDocument();
  });

  it("supports my-driver filtering and bounded pagination", async () => {
    const onLoadMore = vi.fn();
    render(<RaceEventFeed events={events} selectedDriver={4} driverName={(number) => `Driver ${number}`} onLoadMore={onLoadMore} hasMore />);
    await userEvent.click(screen.getByRole("button", { name: "My driver" }));
    expect(screen.getByText(/Driver 4 began closing on Driver 16/i)).toBeVisible();
    expect(screen.queryByText(/entered the pits/i)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /load more events/i }));
    expect(onLoadMore).toHaveBeenCalledOnce();
  });

  it("renders deterministic recent changes separately from conversation", () => {
    render(<RecentChanges events={events} driverName={(number) => `Driver ${number}`} />);
    expect(screen.getByRole("heading", { name: "What changed?" })).toBeVisible();
    expect(screen.getByText(/session was red flagged/i)).toBeVisible();
  });
});
