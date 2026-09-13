// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AnalystStrategyFrame } from "@/components/race-rooms/analyst-strategy-frame";
import {
  controlProjection,
  driverName,
  intelligenceProjection,
  strategyFrame,
  strategySituation,
} from "@/test/strategy-fixtures";
import type { ControlProjection, IntelligenceProjection, StrategyFrame } from "@/lib/types";

function renderFrame(options: {
  frame?: StrategyFrame;
  projection?: IntelligenceProjection | null;
  control?: ControlProjection | null;
} = {}) {
  return render(
    <AnalystStrategyFrame
      frame={options.frame ?? strategyFrame()}
      projection={options.projection === undefined ? intelligenceProjection() : options.projection}
      control={options.control === undefined ? controlProjection() : options.control}
      driverName={driverName}
    />,
  );
}

function qualityNotes(): string[] {
  const heading = screen.getByRole("heading", { name: "Data quality" });
  const list = heading.parentElement?.querySelector("ul");
  return [...(list?.querySelectorAll("li") ?? [])].map((node) => node.textContent ?? "");
}

describe("AnalystStrategyFrame", () => {
  it("exposes the frame identity, clock basis and projection status", () => {
    renderFrame();
    const identity = within(screen.getByRole("region", { name: "Frame identity" }));

    expect(identity.getByText("Analysis time").parentElement).toHaveTextContent(
      "2026-08-10 13:20:00Z",
    );
    expect(identity.getByText("Frame sequence").parentElement).toHaveTextContent("42 (history 41)");
    expect(identity.getByText("Clock basis").parentElement).toHaveTextContent(
      "Monotonic consumed source",
    );
    expect(identity.getByText("Projection").parentElement).toHaveTextContent(
      "Acknowledged at cursor",
    );
    expect(identity.getByText("Projection").parentElement).toHaveTextContent("pipeline: Current");
    expect(identity.getByText("Algorithm").parentElement).toHaveTextContent("strategy-v1");
  });

  it("renders the eight capabilities alongside the situations", () => {
    renderFrame();

    expect(
      screen.getByRole("heading", { name: "What the system can determine" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Situations" })).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "Undercut condition" })).toBeInTheDocument();
  });

  it("says the frame is current but empty rather than showing a blank list", () => {
    renderFrame({ frame: strategyFrame({ situations: [] }) });
    expect(screen.getByText(/no strategy situation is open at this cursor/)).toBeInTheDocument();
  });

  it("keeps a withdrawn situation visible in the frame", () => {
    renderFrame({
      frame: strategyFrame({
        situations: [
          strategySituation({ status: "withdrawn", transition: "withdrawn", limitations: ["pit_window_closed"] }),
        ],
      }),
    });

    expect(
      screen.getByRole("article", { name: "Undercut condition, withdrawn" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Pit window closed")).toBeInTheDocument();
  });

  it("reports truncation, omissions, suppressions and frame limitations honestly", () => {
    renderFrame({
      frame: strategyFrame({
        situations_truncated: true,
        omitted_situations: 4,
        suppressed_events: 7,
        limitations: ["projection_not_current"],
      }),
    });

    const notes = qualityNotes().join(" ");
    expect(notes).toContain("4 were omitted from this frame");
    expect(notes).toContain("7 source events were suppressed before analysis");
    expect(notes).toContain("Frame limitation: Projection not current.");
  });

  it("surfaces truncated control history as a clean-lap uncertainty", () => {
    renderFrame({ control: controlProjection({ history_truncated: true }) });
    expect(qualityNotes().join(" ")).toContain(
      "Control history is truncated, so clean-lap inference since a pit stop is uncertain",
    );
  });

  it("states when no control projection is attached", () => {
    renderFrame({ control: null });
    expect(qualityNotes().join(" ")).toContain("No race control projection is attached");
  });

  it("reports a missing algorithm version rather than inventing one", () => {
    renderFrame({ projection: null });
    const identity = within(screen.getByRole("region", { name: "Frame identity" }));
    expect(identity.getByText("Algorithm").parentElement).toHaveTextContent("Not reported");
  });

  it("counts evidence rows carried by the frame", () => {
    const situation = strategySituation({ evidence_keys: [] });
    renderFrame({ frame: strategyFrame({ situations: [situation], evidence: {} }) });
    const heading = screen.getByRole("heading", { name: "Situations" });
    expect(within(heading.parentElement as HTMLElement).getByText(/0 evidence rows/)).toBeInTheDocument();
  });
});
