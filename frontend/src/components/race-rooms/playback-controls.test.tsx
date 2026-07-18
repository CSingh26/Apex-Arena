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
});
