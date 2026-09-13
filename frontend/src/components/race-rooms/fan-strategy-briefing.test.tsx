// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FanStrategyBriefing } from "@/components/race-rooms/fan-strategy-briefing";
import type { FanNarrative } from "@/lib/strategy-narrative";

function narrative(overrides: Partial<FanNarrative> = {}): FanNarrative {
  return {
    happening: [],
    matters: [],
    watch: [],
    caveats: [],
    ...overrides,
  };
}

describe("FanStrategyBriefing", () => {
  it("asks the three product questions in order", () => {
    render(<FanStrategyBriefing narrative={narrative()} />);
    const headings = screen.getAllByRole("heading", { level: 3 }).map((node) => node.textContent);
    expect(headings).toEqual(["What is happening?", "Why does it matter?", "What to watch next?"]);
  });

  it("renders a populated narrative under each question", () => {
    render(
      <FanStrategyBriefing
        narrative={narrative({
          happening: [{ id: "a", text: "Rain has started falling.", hedged: false, tone: "observation" }],
          matters: [{ id: "b", text: "A wet track changes everything.", hedged: false, tone: "observation" }],
          watch: [{ id: "c", text: "Watch for the first wet tyres.", hedged: false, tone: "observation" }],
        })}
      />,
    );

    expect(screen.getByText("Rain has started falling.")).toBeInTheDocument();
    expect(screen.getByText("A wet track changes everything.")).toBeInTheDocument();
    expect(screen.getByText("Watch for the first wet tyres.")).toBeInTheDocument();
  });

  it("explains an empty section instead of leaving a blank card", () => {
    render(<FanStrategyBriefing narrative={narrative()} />);
    expect(
      screen.getByText(/Nothing in the strategy picture has moved yet/),
    ).toBeInTheDocument();
    expect(screen.getByText(/There is no open strategy call to weigh up/)).toBeInTheDocument();
    expect(screen.getByText(/Nothing specific is building/)).toBeInTheDocument();
  });

  it("badges a hedged statement so an unconfirmed read never reads as fact", () => {
    render(
      <FanStrategyBriefing
        narrative={narrative({
          happening: [
            { id: "a", text: "Rain appears to have started falling.", hedged: true, tone: "observation" },
          ],
        })}
      />,
    );

    const item = screen.getByText("Rain appears to have started falling.").closest("li");
    expect(item).not.toBeNull();
    expect(within(item as HTMLElement).getByText("Not confirmed")).toBeInTheDocument();
  });

  it("badges a withdrawn read as no longer holding", () => {
    render(
      <FanStrategyBriefing
        narrative={narrative({
          happening: [
            { id: "a", text: "Earlier read: something. That no longer holds.", hedged: false, tone: "revision" },
          ],
        })}
      />,
    );

    const item = screen.getByText(/That no longer holds/).closest("li");
    expect(item).toHaveAttribute("data-tone", "revision");
    expect(within(item as HTMLElement).getByText("No longer holds")).toBeInTheDocument();
  });

  it("lists caveats under an explicit uncertainty heading", () => {
    render(
      <FanStrategyBriefing
        narrative={narrative({ caveats: ["Race control history was truncated."] })}
      />,
    );

    expect(screen.getByRole("heading", { name: "What we cannot be sure of" })).toBeInTheDocument();
    expect(screen.getByText("Race control history was truncated.")).toBeInTheDocument();
  });

  it("omits the caveat block entirely when there is nothing to disclose", () => {
    render(<FanStrategyBriefing narrative={narrative()} />);
    expect(screen.queryByRole("heading", { name: "What we cannot be sure of" })).toBeNull();
  });
});
