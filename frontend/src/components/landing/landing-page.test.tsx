// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LandingPage } from "@/components/landing/landing-page";

describe("LandingPage", () => {
  it("introduces Apex Arena before sending visitors into Race Rooms", () => {
    render(<LandingPage />);

    expect(screen.getByRole("heading", { name: /Every race has a story/ })).toBeVisible();
    expect(screen.getByText(/turns live and historical race data/i)).toBeVisible();
    expect(screen.getAllByRole("link", { name: /Race Rooms/i }).some((link) => link.getAttribute("href") === "/race-rooms")).toBe(true);
    expect(screen.getByRole("heading", { name: "Not another timing screen." })).toBeVisible();
    expect(screen.getByRole("heading", { name: /One race.*Five perspectives/i })).toBeVisible();
    expect(screen.getAllByRole("article")).toHaveLength(10);
  });
});
