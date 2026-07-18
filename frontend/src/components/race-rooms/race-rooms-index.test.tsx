// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RaceRoomsIndex } from "@/components/race-rooms/race-rooms-index";
import type { EventSessionSummary, RaceRoomEvent } from "@/lib/types";

const { getRaceRoomEvents } = vi.hoisted(() => ({ getRaceRoomEvents: vi.fn() }));
vi.mock("@/lib/api", () => ({ getRaceRoomEvents }));

function session(overrides: Partial<EventSessionSummary> = {}): EventSessionSummary {
  return {
    session_type: "RACE",
    display_name: "Race",
    scheduled_start: "2026-03-08T04:00:00Z",
    actual_start: "2026-03-08T04:00:00Z",
    status: "completed",
    room_slug: "australian-grand-prix-race",
    room_eligible: true,
    eligibility: "already_exists",
    data_availability: "telemetry",
    replay_available: true,
    results_available: true,
    ...overrides,
  };
}

function weekend(overrides: Partial<RaceRoomEvent> = {}): RaceRoomEvent {
  return {
    event_id: "australia-2026",
    event_slug: "australian-grand-prix-2026",
    meeting_key: "1001",
    season: 2026,
    round: 1,
    event_name: "Australian Grand Prix",
    circuit_name: "Albert Park Grand Prix Circuit",
    country: "Australia",
    weekend_start: "2026-03-06T01:00:00Z",
    weekend_end: "2026-03-08T06:00:00Z",
    weekend_status: "completed",
    is_sprint_weekend: false,
    sessions: [
      session({ session_type: "QUALIFYING", display_name: "Qualifying", scheduled_start: "2026-03-07T05:00:00Z", room_slug: "australian-grand-prix-qualifying" }),
      session(),
    ],
    ...overrides,
  };
}

const completedLater = weekend({ event_id: "japan-2026", event_slug: "japanese-grand-prix-2026", round: 3, event_name: "Japanese Grand Prix", circuit_name: "Suzuka Circuit", country: "Japan", weekend_start: "2026-03-27T02:00:00Z", weekend_end: "2026-03-29T07:00:00Z" });
const liveWeekend = weekend({ event_id: "britain-2026", event_slug: "british-grand-prix-2026", round: 9, event_name: "British Grand Prix", circuit_name: "Silverstone Circuit", country: "United Kingdom", weekend_start: "2026-07-17T10:00:00Z", weekend_end: "2026-07-19T16:00:00Z", weekend_status: "live", sessions: [session({ scheduled_start: "2099-07-18T14:00:00Z", actual_start: null, status: "scheduled", room_slug: null, room_eligible: false, eligibility: "future_read_only", data_availability: "unavailable", replay_available: false, results_available: false })] });
const upcomingSprint = weekend({ event_id: "belgium-2026", event_slug: "belgian-grand-prix-2026", round: 13, event_name: "Belgian Grand Prix", circuit_name: "Circuit de Spa-Francorchamps", country: "Belgium", weekend_start: "2099-07-24T10:00:00Z", weekend_end: "2099-07-26T16:00:00Z", weekend_status: "upcoming", is_sprint_weekend: true, sessions: [
  session({ session_type: "SPRINT_QUALIFYING", display_name: "Sprint Qualifying", scheduled_start: "2099-07-24T15:00:00Z", actual_start: null, status: "scheduled", room_slug: null, room_eligible: false, eligibility: "future_read_only", replay_available: false, results_available: false }),
  session({ session_type: "SPRINT", display_name: "Sprint", scheduled_start: "2099-07-25T10:00:00Z", actual_start: null, status: "scheduled", room_slug: null, room_eligible: false, eligibility: "future_read_only", replay_available: false, results_available: false }),
  session({ session_type: "QUALIFYING", display_name: "Qualifying", scheduled_start: "2099-07-25T14:00:00Z", actual_start: null, status: "scheduled", room_slug: null, room_eligible: false, eligibility: "future_read_only", replay_available: false, results_available: false }),
  session({ scheduled_start: "2099-07-26T13:00:00Z", actual_start: null, status: "scheduled", room_slug: null, room_eligible: false, eligibility: "future_read_only", replay_available: false, results_available: false }),
] });
const validationWeekend = weekend({ event_id: "validation", event_slug: "private-validation-room", event_name: "Private Validation Room", is_development: true });

describe("RaceRoomsIndex", () => {
  beforeEach(() => {
    getRaceRoomEvents.mockClear();
    window.history.replaceState(null, "", "/race-rooms");
    getRaceRoomEvents.mockResolvedValue({ events: [upcomingSprint, completedLater, validationWeekend, liveWeekend, weekend()], total: 4, limit: 100, offset: 0 });
  });

  it("uses the themed race loader while the schedule is pending", () => {
    getRaceRoomEvents.mockReturnValue(new Promise(() => undefined));
    render(<RaceRoomsIndex />);
    expect(screen.getByRole("status", { name: "Mapping the 2026 race grid" })).toBeVisible();
  });

  it("renders three grouped categories, concise session actions, and excludes validation fixtures", async () => {
    render(<RaceRoomsIndex />);
    expect(screen.getByRole("heading", { name: "Race Rooms" })).toBeVisible();
    expect(await screen.findByRole("heading", { name: "Live This Weekend" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Completed Events" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Upcoming Events" })).toBeVisible();
    expect(screen.getByText("Live feed arms at session start")).toBeVisible();
    expect(screen.queryByText(/Validation Room/)).not.toBeInTheDocument();
    expect(screen.queryByText(/archived/i)).not.toBeInTheDocument();

    const completed = screen.getByRole("heading", { name: "Completed Events" }).closest("section");
    expect(completed).not.toBeNull();
    const headings = within(completed as HTMLElement).getAllByRole("heading", { level: 3 });
    expect(headings.map((heading) => heading.textContent)).toEqual(["Australian Grand Prix", "Japanese Grand Prix"]);
    expect(within(completed as HTMLElement).getByRole("link", { name: /Open Australian Grand Prix Qualifying/ })).toHaveAttribute("href", "/race-rooms/australian-grand-prix-qualifying");
    expect(within(completed as HTMLElement).getByRole("img", { name: "Australian Grand Prix 2026 circuit layout" })).toBeVisible();

    const sprintCard = screen.getByRole("heading", { name: "Belgian Grand Prix" }).closest("article");
    expect(sprintCard).not.toBeNull();
    expect(within(sprintCard as HTMLElement).getByText("Sprint weekend")).toBeVisible();
    expect(within(sprintCard as HTMLElement).getByText("Sprint Qualifying")).toBeVisible();
    expect(within(sprintCard as HTMLElement).getByText("Sprint")).toBeVisible();
  });

  it("counts down to the nearest future session instead of a recently completed session", async () => {
    getRaceRoomEvents.mockResolvedValue({
      events: [weekend({
        weekend_status: "live",
        sessions: [
          session({ session_type: "QUALIFYING", display_name: "Qualifying", scheduled_start: "2000-01-01T10:00:00Z", status: "completed" }),
          session({ display_name: "Race", scheduled_start: "2099-01-02T10:00:00Z", actual_start: null, status: "scheduled", room_slug: null }),
        ],
      })],
      total: 1,
      limit: 100,
      offset: 0,
    });

    render(<RaceRoomsIndex />);
    expect(await screen.findByRole("heading", { name: "Race", level: 2 })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Qualifying", level: 2 })).not.toBeInTheDocument();
  });

  it("opens an upcoming weekend as a read-only schedule and returns with browser history", async () => {
    const user = userEvent.setup();
    render(<RaceRoomsIndex />);
    const preview = await screen.findByRole("button", { name: /View schedule for Belgian Grand Prix Sprint Qualifying/ });
    await user.click(preview);
    const dialog = screen.getByRole("dialog", { name: "Belgian Grand Prix" });
    expect(within(dialog).getByRole("heading", { name: "Weekend schedule" })).toBeVisible();
    expect(within(dialog).getByText("Room opens when session data becomes available.")).toBeVisible();
    expect(screen.queryByText("Session conversation")).not.toBeInTheDocument();
    expect(window.location.search).toContain("event=belgian-grand-prix-2026");
    expect(getRaceRoomEvents).toHaveBeenCalledTimes(1);

    await user.click(within(dialog).getByRole("button", { name: "Back to events" }));
    window.dispatchEvent(new PopStateEvent("popstate"));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("sends grouped event filters to the API and exposes compact mobile filters", async () => {
    const user = userEvent.setup();
    render(<RaceRoomsIndex />);
    await screen.findByRole("heading", { name: "Completed Events" });
    await user.click(screen.getByRole("button", { name: /All events/ }));
    await user.type(screen.getByPlaceholderText("Grand Prix, circuit or country"), "Spa");
    await user.selectOptions(screen.getByLabelText("Category"), "upcoming");
    await user.selectOptions(screen.getByLabelText("Session"), "SPRINT");
    await user.selectOptions(screen.getByLabelText("Weekend"), "sprint");
    await waitFor(() => {
      const params = getRaceRoomEvents.mock.calls.at(-1)?.[0] as URLSearchParams;
      expect(params.get("season")).toBe("2026");
      expect(params.get("search")).toBe("Spa");
      expect(params.get("status")).toBe("upcoming");
      expect(params.get("session_type")).toBe("SPRINT");
      expect(params.get("is_sprint_weekend")).toBe("true");
    });
  });
});
