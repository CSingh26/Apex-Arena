// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ApexRaceLoader } from "@/components/loading/apex-race-loader";

describe("ApexRaceLoader", () => {
  it("announces its loading state without exposing decorative motion", () => {
    render(<ApexRaceLoader label="Joining the live room" />);
    expect(screen.getByRole("status", { name: "Joining the live room" })).toBeVisible();
    expect(screen.getByText("APEX")).toBeVisible();
  });
});
