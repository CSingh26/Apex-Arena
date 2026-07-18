// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { AppNavigation } from "@/components/navigation/app-navigation";

describe("AppNavigation", () => {
  it("keeps the brand, active Race Rooms route, and an accessible mobile menu", async () => {
    window.history.replaceState(null, "", "/race-rooms");
    const user = userEvent.setup();
    render(<AppNavigation contextLabel="Belgian Grand Prix · Race" />);

    expect(screen.getByRole("link", { name: "Apex Arena home" })).toBeVisible();
    expect(screen.getAllByRole("link", { name: "Race Rooms" })[0]).toHaveAttribute("aria-current", "page");
    const menuButton = screen.getByRole("button", { name: "Open navigation menu" });
    await user.click(menuButton);
    const menu = screen.getByRole("dialog", { name: "Mobile navigation" });
    expect(menu).toBeVisible();
    expect(within(menu).getByText("Belgian Grand Prix · Race")).toBeVisible();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Mobile navigation" })).not.toBeInTheDocument());
    expect(menuButton).toHaveFocus();
  });

  it("preserves the landing-page section links", () => {
    window.history.replaceState(null, "", "/");
    render(<AppNavigation />);
    expect(screen.getAllByRole("link", { name: "Experience" })[0]).toHaveAttribute("href", "#experience");
    expect(screen.getAllByRole("link", { name: "The room" })[0]).toHaveAttribute("href", "#agents");
  });
});
