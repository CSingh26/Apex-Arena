// SPDX-License-Identifier: AGPL-3.0-only
import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { MessageTimeline } from "@/components/race-rooms/message-timeline";
import type { RoomMessage } from "@/lib/types";
import { agents, message } from "@/test/race-room-fixtures";

const strategy = message();
const pace = message({ id: "00000000-0000-0000-0000-000000000002", sequence: 2, agent_id: "theo-voss", lap_number: 9, topic: "pace", content: "The representative lap trend improved by 0.68 seconds." });
const reply = message({ id: "00000000-0000-0000-0000-000000000003", sequence: 3, agent_id: "lena-cross", topic: "racecraft", message_type: "disagreement", content: "Track position still limits that strategy gain.", reply_to_message_id: strategy.id });

function TimelineHarness({ items = [strategy, pace, reply] }: { items?: RoomMessage[] }) {
  const [agent, setAgent] = useState("all");
  return <MessageTimeline messages={items} agents={agents} selectedAgent={agent} totalLaps={12} hasMore={false} loadingMore={false} onSelectedAgentChange={setAgent} onLoadMore={vi.fn()} onInspectEvidence={vi.fn()} />;
}

describe("MessageTimeline", () => {
  it("renders editorial reply relationships and filters by agent, topic, and lap", async () => {
    const user = userEvent.setup();
    render(<TimelineHarness />);
    expect(screen.getByText(/Replying to Mira Vale/)).toBeVisible();
    expect(screen.getByText("Track position still limits that strategy gain.")).toBeVisible();

    await user.selectOptions(screen.getByLabelText("Voice"), "mira-vale");
    expect(screen.getByText(strategy.content)).toBeVisible();
    expect(screen.queryByText(pace.content)).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Voice"), "all");
    await user.selectOptions(screen.getByLabelText("Topic"), "pace");
    expect(screen.getByText(pace.content)).toBeVisible();
    expect(screen.queryByText(strategy.content)).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Topic"), "all");
    await user.type(screen.getByLabelText("Lap"), "6");
    expect(screen.getByText(strategy.content)).toBeVisible();
    expect(screen.queryByText(pace.content)).not.toBeInTheDocument();
  });

  it("shows a purposeful replay empty state and a jump-to-latest control", async () => {
    render(<TimelineHarness items={[]} />);
    expect(screen.getByText("The room is waiting for lights out.")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: /jump to latest/i }));
    expect(HTMLElement.prototype.scrollIntoView).toHaveBeenCalled();
  });
});
