// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StrategySituationCard } from "@/components/race-rooms/strategy-situation-card";
import {
  driverName,
  strategyEvidence,
  strategyFrame,
  strategyPayload,
  strategySituation,
} from "@/test/strategy-fixtures";
import type { StrategyFrame, StrategySituation } from "@/lib/types";

function renderCard(situation: StrategySituation, frame?: StrategyFrame) {
  const resolvedFrame = frame ?? strategyFrame({ situations: [situation] });
  return render(
    <StrategySituationCard
      frame={resolvedFrame}
      situation={situation}
      driverName={driverName}
    />,
  );
}

function definition(label: string): string {
  const term = screen.getByText(label);
  const value = term.parentElement?.querySelector("dd");
  return value?.textContent ?? "";
}

describe("StrategySituationCard", () => {
  it("shows kind, participants, status and confidence", () => {
    renderCard(strategySituation());

    expect(screen.getByRole("heading", { name: "Undercut condition" })).toBeInTheDocument();
    expect(screen.getByText("undercut_condition")).toBeInTheDocument();
    expect(screen.getByText("Lando Norris vs Charles Leclerc")).toBeInTheDocument();
    expect(screen.getByText("active")).toBeInTheDocument();
    expect(screen.getByText("observed")).toBeInTheDocument();
  });

  it("renders payload values with units and F1 timing formatting", () => {
    renderCard(
      strategySituation({
        payload: strategyPayload({
          gap_seconds: 2.031,
          required_gain_seconds: 1.284,
          remaining_laps: 18,
        }),
      }),
    );

    expect(definition("Gap")).toBe("+2.031");
    expect(definition("Required gain")).toBe("1.284s");
    expect(definition("Remaining laps")).toBe("18 laps");
  });

  it("says so when a situation carries no measured values", () => {
    renderCard(strategySituation({ payload: strategyPayload() }));
    expect(screen.getByText(/carries no measured values/)).toBeInTheDocument();
  });

  it("lists the fields the system cannot determine", () => {
    renderCard(strategySituation());
    const heading = screen.getByRole("heading", { name: "Not determined by this system" });
    const list = heading.parentElement?.querySelector("ul");
    expect(list).not.toBeNull();
    expect(within(list as HTMLElement).getByText("Outcome")).toBeInTheDocument();
    expect(within(list as HTMLElement).getByText("Rival stop timing")).toBeInTheDocument();
  });

  it("humanises assumptions and limitations", () => {
    renderCard(
      strategySituation({
        assumptions: ["green_conditions_only"],
        limitations: ["approximate_lap_interval"],
      }),
    );

    expect(screen.getByText("Green conditions only")).toBeInTheDocument();
    expect(screen.getByText("Approximate lap interval")).toBeInTheDocument();
  });

  it("renders evidence rows with role, source and observation time", () => {
    const situation = strategySituation({ evidence_keys: ["event-a:pit"] });
    const frame = strategyFrame({
      situations: [situation],
      evidence: {
        "event-a:pit": strategyEvidence({ source: "openf1", observed_at: "2026-08-10T13:19:30Z" }),
      },
    });
    renderCard(situation, frame);

    expect(screen.getByText("Evidence (1)")).toBeInTheDocument();
    const row = screen.getByText("openf1").closest("tr");
    expect(within(row as HTMLElement).getByText("pit")).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText("2026-08-10 13:19:30Z")).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText(/Car 4 · Lap 23 · Stint 2/)).toBeInTheDocument();
  });

  it("reports evidence keys the frame failed to close over", () => {
    const situation = strategySituation({ evidence_keys: ["ghost:pit"] });
    renderCard(situation, strategyFrame({ situations: [situation], evidence: {} }));

    expect(screen.getByText("Evidence (0, 1 unresolved)")).toBeInTheDocument();
    expect(screen.getByText(/referenced but not present in the frame/)).toBeInTheDocument();
  });

  it("renders a withdrawn situation as a visible revision", () => {
    renderCard(
      strategySituation({
        status: "withdrawn",
        transition: "withdrawn",
        superseded_revision_id: "99999999-9999-4999-8999-999999999999",
      }),
    );

    expect(
      screen.getByRole("article", { name: "Undercut condition, withdrawn" }),
    ).toHaveAttribute("data-status", "withdrawn");
    expect(screen.getByText(/Withdrawn at sequence 42/)).toBeInTheDocument();
    expect(screen.getByText(/superseded revision 99999999/)).toBeInTheDocument();
  });

  it("marks a partial situation availability on the card", () => {
    renderCard(strategySituation({ availability: "partial", observation_confidence: "qualified" }));
    expect(screen.getByRole("article", { name: "Undercut condition" })).toHaveAttribute(
      "data-availability",
      "partial",
    );
    expect(screen.getByText("qualified")).toBeInTheDocument();
  });

  it("states when no participants were recorded rather than showing an empty line", () => {
    renderCard(strategySituation({ participants: [] }));
    expect(screen.getByText("No driver participants recorded")).toBeInTheDocument();
  });
});
