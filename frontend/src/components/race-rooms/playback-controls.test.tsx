// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PlaybackControls } from "@/components/race-rooms/playback-controls";
import { playback, room } from "@/test/race-room-fixtures";

describe("PlaybackControls", () => {
  it("starts replay and sends exact pause, speed, and seek actions", async () => {
    const user = userEvent.setup();
    const onReplay = vi.fn().mockResolvedValue(undefined);
    const onControl = vi.fn().mockResolvedValue(undefined);
    const { rerender } = render(<PlaybackControls room={room} playback={playback} busy={false} error={null} onReplay={onReplay} onControl={onControl} />);
    await user.click(screen.getByRole("button", { name: /start replay/i }));
    expect(onReplay).toHaveBeenCalledWith("start");

    const started = { ...playback, started_at: playback.updated_at, current_event_sequence: 3, current_lap: 2, is_paused: false };
    rerender(<PlaybackControls room={{ ...room, status: "replaying" }} playback={started} busy={false} error={null} onReplay={onReplay} onControl={onControl} />);
    await user.click(screen.getByRole("button", { name: /pause/i }));
    expect(onControl).toHaveBeenCalledWith({ action: "pause" });
    await user.selectOptions(screen.getByLabelText("Playback speed"), "4");
    expect(onControl).toHaveBeenCalledWith({ action: "set_speed", playback_speed: 4 });
    await user.click(screen.getByRole("button", { name: /seek/i }));
    await user.clear(screen.getByLabelText("Lap"));
    await user.type(screen.getByLabelText("Lap"), "9");
    await user.click(screen.getByRole("button", { name: /go to lap/i }));
    expect(onControl).toHaveBeenCalledWith({ action: "seek_to_lap", lap_number: 9 });
  });

  it("uses phase and session-time controls for qualifying instead of race laps", async () => {
    const user = userEvent.setup();
    const onControl = vi.fn().mockResolvedValue(undefined);
    const qualifyingRoom = { ...room, session_type: "SPRINT_QUALIFYING", current_phase: "SQ2", status: "replaying" as const };
    const started = { ...playback, started_at: playback.updated_at, current_event_sequence: 3, current_lap: null, is_paused: true };
    render(<PlaybackControls room={qualifyingRoom} playback={started} busy={false} error={null} onReplay={vi.fn()} onControl={onControl} />);

    expect(screen.getByText("SQ2")).toBeVisible();
    expect(screen.queryByText(/Lap data/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /seek/i }));
    await user.click(screen.getByRole("button", { name: "SQ3" }));
    expect(onControl).toHaveBeenCalledWith({ action: "seek_to_phase", phase: "SQ3" });
    await user.clear(screen.getByLabelText("Session time (seconds)"));
    await user.type(screen.getByLabelText("Session time (seconds)"), "600");
    await user.click(screen.getByRole("button", { name: "Go to time" }));
    expect(onControl).toHaveBeenCalledWith({ action: "seek_to_session_time", session_time: 600 });
    expect(screen.queryByLabelText("Lap")).not.toBeInTheDocument();
  });
});
