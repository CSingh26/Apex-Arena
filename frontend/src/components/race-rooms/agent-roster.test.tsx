// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AgentRoster } from "@/components/race-rooms/agent-roster";
import { agents } from "@/test/race-room-fixtures";

describe("AgentRoster", () => {
  it("renders all five persistent agent profiles and collapses accessibly", async () => {
    const user = userEvent.setup();
    render(<AgentRoster agents={agents} selectedAgent="all" onSelectAgent={vi.fn()} />);

    expect(screen.getByText("5 agents in this room")).toBeVisible();
    expect(screen.queryByText("Race Strategist")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /expand agent roster/i }));
    expect(screen.getAllByRole("button", { pressed: false })).toHaveLength(5);
    expect(screen.getByText("Mira Vale")).toBeVisible();
    expect(screen.getByText("Nova")).toBeVisible();
    await user.click(screen.getByRole("button", { name: /collapse agent roster/i }));
    expect(screen.queryByText("Race Strategist")).not.toBeInTheDocument();
    expect(screen.getByText("5 agents in this room")).toBeVisible();
  });

  it("selects an agent as a timeline filter", async () => {
    const onSelect = vi.fn();
    render(<AgentRoster agents={agents} selectedAgent="all" onSelectAgent={onSelect} />);
    await userEvent.click(screen.getByRole("button", { name: /expand agent roster/i }));
    await userEvent.click(screen.getByRole("button", { name: /Mira Vale/i }));
    expect(onSelect).toHaveBeenCalledWith("mira-vale");
  });
});
