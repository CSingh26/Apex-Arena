// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CircuitOutline, circuitAssetId } from "@/components/race-rooms/circuit-outline";

describe("CircuitOutline", () => {
  it("selects the season-correct library asset and exposes an accessible label", () => {
    expect(circuitAssetId("Circuit de Spa-Francorchamps")).toBe("spa-francorchamps-4");
    expect(circuitAssetId("Unknown circuit")).toBeNull();
    render(<CircuitOutline circuitName="Circuit de Spa-Francorchamps" eventName="Belgian Grand Prix" />);
    expect(screen.getByRole("img", { name: "Belgian Grand Prix 2026 circuit layout" })).toBeVisible();
    expect(document.querySelector('img[src="/circuits/2026/white-outline/spa-francorchamps-4.svg"]')).toBeInTheDocument();
  });

  it("does not invent a silhouette for an unknown circuit", () => {
    render(<CircuitOutline circuitName="Unknown circuit" />);
    expect(screen.getByRole("img", { name: "Unknown circuit 2026 circuit layout unavailable" })).toBeVisible();
  });
});
