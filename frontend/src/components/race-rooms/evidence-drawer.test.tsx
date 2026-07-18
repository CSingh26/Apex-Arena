// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { EvidenceDrawer } from "@/components/race-rooms/evidence-drawer";
import { agents, message } from "@/test/race-room-fixtures";

const getMessageEvidence = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ getMessageEvidence }));

describe("EvidenceDrawer", () => {
  it("shows trigger, quality, generation, confidence, and supporting facts", async () => {
    const selected = message();
    getMessageEvidence.mockResolvedValue({
      message_id: selected.id,
      evidence: [{ id: "evidence-1", message_id: selected.id, evidence_key: "pit_duration", evidence_type: "normalized_event", source_provider: "apex_day3_fixture", source_reference: "event-7", metric_name: "Pit duration", metric_value: 2.41, unit: "s", context: {}, created_at: selected.created_at }],
      trigger_event: { event_id: "event-7", event_sequence: 7, lap_number: 6, source_provider: "apex_day3_fixture" },
      snapshot_reference: null, data_quality_flags: ["complete"], generation_mode: "deterministic", confidence: "high",
    });
    const onClose = vi.fn();
    render(<EvidenceDrawer slug="day3-validation-room" message={selected} agent={agents[0]} onClose={onClose} />);

    expect(await screen.findByText("Event #7")).toBeVisible();
    expect(screen.getByText("Pit duration")).toBeVisible();
    expect(screen.getAllByText(/2.41/)).toHaveLength(2);
    expect(screen.getByText("complete")).toBeVisible();
    expect(screen.getByText("deterministic")).toBeVisible();
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("keeps keyboard focus inside the modal and restores it when closed", async () => {
    const selected = message();
    getMessageEvidence.mockResolvedValue({ message_id: selected.id, evidence: [], trigger_event: null, snapshot_reference: null, data_quality_flags: [], generation_mode: "deterministic", confidence: "high" });
    const opener = document.createElement("button");
    opener.textContent = "Evidence opener";
    document.body.append(opener);
    opener.focus();
    const { unmount } = render(<EvidenceDrawer slug="day3-validation-room" message={selected} agent={agents[0]} onClose={vi.fn()} />);
    const close = within(await screen.findByRole("dialog")).getByRole("button", { name: "Close evidence panel" });
    await waitFor(() => expect(close).toHaveFocus());
    await userEvent.tab();
    expect(close).toHaveFocus();
    unmount();
    expect(opener).toHaveFocus();
    opener.remove();
  });
});
