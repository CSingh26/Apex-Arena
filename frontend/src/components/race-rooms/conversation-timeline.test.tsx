// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ConversationTimeline } from "@/components/race-rooms/conversation-timeline";
import { message } from "@/test/race-room-fixtures";

describe("ConversationTimeline", () => {
  it("maps race laps to clickable key moments", async () => {
    const onSelect = vi.fn();
    render(<ConversationTimeline messages={[message({ sequence: 2, lap_number: 6 }), message({ id: "00000000-0000-0000-0000-000000000009", sequence: 9, lap_number: 9, topic: "pace" })]} totalLaps={12} sessionType="RACE" onSelect={onSelect} />);
    expect(screen.getByRole("heading", { name: "Session timeline" })).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Jump to L9, pace" }));
    expect(onSelect).toHaveBeenCalledWith(9);
  });

  it("uses qualifying phases instead of meaningless lap zero", () => {
    render(<ConversationTimeline messages={[message({ session_phase: "Q1", lap_number: null }), message({ id: "00000000-0000-0000-0000-000000000010", sequence: 10, session_phase: "Q2", lap_number: null })]} totalLaps={null} sessionType="QUALIFYING" onSelect={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Jump to Q1/ })).toBeVisible();
    expect(screen.getByRole("button", { name: /Jump to Q2/ })).toBeVisible();
  });
});
