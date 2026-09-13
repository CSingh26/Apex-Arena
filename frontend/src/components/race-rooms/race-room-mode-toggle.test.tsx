// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { RaceRoomModeToggle } from "./race-room-mode-toggle";

describe("RaceRoomModeToggle", () => {
  beforeEach(() => window.localStorage.clear());

  it("defaults to Fan and persists an Analyst preference", async () => {
    render(<RaceRoomModeToggle />);
    expect(screen.getByRole("button", { name: "Fan" })).toHaveAttribute("aria-pressed", "true");

    await userEvent.click(screen.getByRole("button", { name: "Analyst" }));
    expect(screen.getByRole("button", { name: "Analyst" })).toHaveAttribute("aria-pressed", "true");
    expect(window.localStorage.getItem("apex-arena-race-room-mode")).toBe("ANALYST");
  });

  it("restores a saved preference", () => {
    window.localStorage.setItem("apex-arena-race-room-mode", "ANALYST");
    render(<RaceRoomModeToggle />);
    expect(screen.getByRole("button", { name: "Analyst" })).toHaveAttribute("aria-pressed", "true");
  });
});
