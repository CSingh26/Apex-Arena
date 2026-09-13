// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  AnalystBattleContext,
  FanBattleContext,
} from "@/components/race-rooms/battle-strategy-context";
import { battleContext, driverName, strategyEvidence } from "@/test/strategy-fixtures";
import type { StrategyBattleContext } from "@/lib/types";

const SUBJECT = { leadDriverNumber: 16, chasingDriverNumber: 4, driverName };

function renderFan(context: StrategyBattleContext) {
  return render(<FanBattleContext context={context} {...SUBJECT} />);
}

function renderAnalyst(context: StrategyBattleContext) {
  return render(<AnalystBattleContext context={context} {...SUBJECT} />);
}

function undeterminedList(): HTMLElement {
  const heading = screen.getByRole("heading", { name: "Not determined by this system" });
  const list = heading.parentElement?.querySelector("ul");
  if (!list) throw new Error("expected an undetermined list");
  return list as HTMLElement;
}

describe("FanBattleContext", () => {
  it("says why a populated battle is worth watching, in plain words", () => {
    renderFan(
      battleContext({
        closing: true,
        within_one_second: true,
        same_reported_team: true,
        duration_seconds: 210,
        remaining_laps: 8,
      }),
    );

    const list = screen.getByRole("list", { name: "Why this battle matters" });
    expect(within(list).getByText("Lando Norris is taking time out of the car ahead.")).toBeVisible();
    expect(within(list).getByText("The two are running inside a second of each other.")).toBeVisible();
    expect(within(list).getByText(/team mates/)).toBeVisible();
    expect(within(list).getByText("This fight has been running for 3 minutes 30 seconds.")).toBeVisible();
    expect(within(list).getByText("8 laps remain to settle it.")).toBeVisible();
  });

  it("never turns proximity or a track DRS state into a DRS attack", () => {
    const { container } = renderFan(
      battleContext({ within_one_second: true, drs_permission: "enabled" }),
    );
    const text = (container.textContent ?? "").toLowerCase();

    expect(text).toContain("inside a second");
    expect(text).not.toContain("drs attack");
    expect(text).not.toContain("using drs");
  });

  it("names what it cannot tell instead of implying a negative", () => {
    renderFan(battleContext({ closing: true }));

    expect(screen.getByText(/We cannot tell whether a move has been attempted/)).toBeVisible();
    expect(screen.queryByText(/no overtake attempts/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/not championship relevant/i)).not.toBeInTheDocument();
  });

  it("still carries the caveat when the engine determined nothing else", () => {
    renderFan(battleContext());

    expect(screen.queryByRole("list", { name: "Why this battle matters" })).not.toBeInTheDocument();
    expect(screen.getByText(/We cannot tell/)).toBeVisible();
  });
});

describe("AnalystBattleContext", () => {
  it("shows measured values, prominence and evidence for a populated context", () => {
    renderAnalyst(
      battleContext({
        closing: true,
        within_one_second: true,
        observed_wing_open: true,
        drs_permission: "enabled",
        duration_seconds: 94.5,
        remaining_laps: 12,
        train_members: [16, 4, 63],
        evidence: { "event-a:interval": strategyEvidence({ role: "interval", sequence: 12 }) },
        limitations: ["approximate_lap_interval"],
      }),
    );

    const values = screen.getByRole("region", { name: /^Battle context for/ });
    expect(within(values).getByText("Closing")).toBeVisible();
    expect(within(values).getByText("1:34.500s")).toBeVisible();
    expect(within(values).getByText("Enabled")).toBeVisible();
    expect(within(values).getByText("Car 16, Car 4, Car 63")).toBeVisible();

    const prominence = screen.getByRole("region", { name: /^Prominence for/ });
    expect(within(prominence).getByText("63")).toBeVisible();
    expect(within(prominence).getByText("Green timing pressure")).toBeVisible();

    expect(screen.getByText("Approximate lap interval")).toBeVisible();
    // Evidence lives in a collapsed drawer, so it is present rather than shown.
    expect(screen.getByText("Evidence (1)")).toBeVisible();
    expect(screen.getByText("interval")).toBeInTheDocument();
  });

  it("labels the pinned literals as undetermined, never as negatives", () => {
    renderAnalyst(battleContext());
    const list = undeterminedList();

    expect(within(list).getByText("Overtake attempts")).toBeVisible();
    expect(within(list).getByText("Championship relevance")).toBeVisible();
    expect(screen.queryByText(/no overtake attempt/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/not championship relevant/i)).not.toBeInTheDocument();
  });

  it("lists every partially determined field as undetermined", () => {
    renderAnalyst(battleContext({ within_one_second: true }));
    const list = undeterminedList();

    expect(within(list).queryByText("Within one second (proximity)")).not.toBeInTheDocument();
    expect(within(list).getByText("Rear wing observed open")).toBeVisible();
    expect(within(list).getByText("DRS permission (track state)")).toBeVisible();
    expect(within(list).getByText("Tyre context")).toBeVisible();
    expect(within(list).getByText("Relative pace")).toBeVisible();
  });

  it("says the context carries no evidence rather than showing an empty table", () => {
    renderAnalyst(battleContext());

    expect(screen.getByText("Evidence (0)")).toBeVisible();
    expect(screen.getByText("This battle context carries no evidence rows.")).toBeInTheDocument();
  });
});
