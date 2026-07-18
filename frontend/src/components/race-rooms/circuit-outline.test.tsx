// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CircuitOutline, circuitPath } from "@/components/race-rooms/circuit-outline";

describe("CircuitOutline", () => {
  it("selects named silhouettes and exposes an accessible label", () => {
    expect(circuitPath("Circuit de Spa-Francorchamps")).not.toBe(circuitPath("Unknown circuit"));
    render(<CircuitOutline circuitName="Circuit de Spa-Francorchamps" eventName="Belgian Grand Prix" />);
    expect(screen.getByRole("img", { name: "Belgian Grand Prix circuit outline" })).toBeVisible();
  });
});
