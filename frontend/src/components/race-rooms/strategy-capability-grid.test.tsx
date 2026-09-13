// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StrategyCapabilityGrid } from "@/components/race-rooms/strategy-capability-grid";
import { strategyFrame } from "@/test/strategy-fixtures";

function renderGrid(capabilities?: ReturnType<typeof strategyFrame>["capabilities"]) {
  const frame = capabilities ? strategyFrame({ capabilities }) : strategyFrame();
  return render(<StrategyCapabilityGrid frame={frame} headingId="capabilities" />);
}

function itemFor(label: string): HTMLElement {
  const item = screen.getByText(label).closest("li");
  if (!item) throw new Error(`no capability item for ${label}`);
  return item;
}

describe("StrategyCapabilityGrid", () => {
  it("always lists all eight contract capabilities", () => {
    renderGrid();
    expect(screen.getAllByRole("listitem")).toHaveLength(8);
    expect(screen.getByText("All 8 checks are running")).toBeInTheDocument();
  });

  it("shows the humanised reason for an unavailable capability", () => {
    renderGrid({
      ...strategyFrame().capabilities,
      extra_stop_consequence: {
        availability: "unavailable",
        reason: "missing_authoritative_remaining_distance",
      },
    });

    const item = itemFor("Extra stop consequence");
    expect(item).toHaveAttribute("data-availability", "unavailable");
    expect(within(item).getByText(/Cannot be determined right now/)).toBeInTheDocument();
    expect(
      within(item).getByText(/Missing authoritative remaining distance/),
    ).toBeInTheDocument();
    expect(screen.getByText("1 of 8 checks cannot run")).toBeInTheDocument();
  });

  it("marks a partial capability as qualified rather than confirmed", () => {
    renderGrid({
      ...strategyFrame().capabilities,
      relative_pace: { availability: "partial", reason: "insufficient_green_samples" },
    });

    const item = itemFor("Relative pace");
    expect(item).toHaveAttribute("data-availability", "partial");
    expect(within(item).getByText(/the read is qualified/)).toBeInTheDocument();
    expect(within(item).getByText(/Insufficient green samples/)).toBeInTheDocument();
    // Partial is still running, so it is not counted as blocked.
    expect(screen.getByText("All 8 checks are running")).toBeInTheDocument();
  });

  it("explains an omitted capability as a deliberate exclusion", () => {
    renderGrid({
      ...strategyFrame().capabilities,
      weather_change: { availability: "omitted", reason: "not_applicable_to_view" },
    });

    const item = itemFor("Weather change");
    expect(within(item).getByText(/Deliberately not produced for this view/)).toBeInTheDocument();
  });

  it("defaults a capability the backend failed to send rather than hiding the slot", () => {
    renderGrid({});
    expect(screen.getAllByRole("listitem")).toHaveLength(8);
    expect(screen.getByText("8 of 8 checks cannot run")).toBeInTheDocument();
    expect(screen.getAllByText(/Missing capability/)).toHaveLength(8);
  });
});
