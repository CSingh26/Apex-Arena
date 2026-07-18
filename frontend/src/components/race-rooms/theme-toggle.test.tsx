// SPDX-License-Identifier: AGPL-3.0-only
import { act } from "react";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ThemeToggle } from "@/components/race-rooms/theme-toggle";

afterEach(() => {
  window.localStorage.clear();
  delete document.documentElement.dataset.theme;
});

describe("ThemeToggle", () => {
  it("hydrates deterministically before applying a persisted theme", async () => {
    window.localStorage.setItem("apex-arena-theme", "light");
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const container = document.createElement("div");
    container.innerHTML = renderToString(<ThemeToggle />);
    document.body.append(container);

    const root = hydrateRoot(container, <ThemeToggle />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Switch to dark mode" })).toBeVisible());

    expect(document.documentElement.dataset.theme).toBe("light");
    expect(consoleError).not.toHaveBeenCalled();
    await act(async () => root.unmount());
    container.remove();
    consoleError.mockRestore();
  });
});
