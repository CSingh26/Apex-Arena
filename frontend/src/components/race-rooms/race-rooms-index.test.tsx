// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RaceRoomsIndex } from "@/components/race-rooms/race-rooms-index";
import { room } from "@/test/race-room-fixtures";

const getRaceRooms = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ getRaceRooms }));

const archivedRoom = { ...room, id: "00000000-0000-0000-0000-000000000099", slug: "archived-race", race_name: "Archived Grand Prix", is_featured: false, is_development: false, mode: "archived" as const, status: "completed" as const };
const unavailableFeatured = { ...room, id: "00000000-0000-0000-0000-000000000098", slug: "upcoming-race", race_name: "Upcoming Grand Prix", session_key: null, is_development: false, status: "unavailable" as const, mode: "replay" as const, source_availability: "unavailable" as const };

describe("RaceRoomsIndex", () => {
  it("renders the featured validation room and a true completed-race archive", async () => {
    getRaceRooms.mockResolvedValue({ rooms: [unavailableFeatured, room, archivedRoom], total: 3, limit: 100, offset: 0 });
    render(<RaceRoomsIndex />);
    expect(screen.getByRole("heading", { name: "Race Rooms" })).toBeVisible();
    expect(screen.getByText(/Enter live and archived rooms/)).toBeVisible();
    expect(await screen.findByRole("heading", { name: room.race_name })).toBeVisible();
    expect(screen.getByText("Replay conversation")).toBeVisible();
    expect(screen.getByText("Season archive")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Completed races" })).toBeVisible();
    expect(screen.getByRole("heading", { name: archivedRoom.race_name })).toBeVisible();
  });

  it("sends search, season, and status filters to the list API without a page reset", async () => {
    getRaceRooms.mockResolvedValue({ rooms: [room], total: 1, limit: 100, offset: 0 });
    render(<RaceRoomsIndex />);
    await screen.findByRole("heading", { name: room.race_name });
    await userEvent.type(screen.getByPlaceholderText("Race, circuit or country"), "Validation");
    await userEvent.selectOptions(screen.getByLabelText("Status"), "ready");
    await waitFor(() => expect(getRaceRooms).toHaveBeenCalledWith(expect.objectContaining({}), expect.any(AbortSignal)));
    await waitFor(() => {
      const params = getRaceRooms.mock.calls.at(-1)?.[0] as URLSearchParams;
      expect(params.get("season")).toBe("2026");
      expect(params.get("search")).toBe("Validation");
      expect(params.get("status")).toBe("ready");
    });
    expect(screen.getByRole("heading", { name: room.race_name })).toBeVisible();
  });
});
