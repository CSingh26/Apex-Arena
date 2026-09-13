// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StrategyPanel } from "@/components/race-rooms/strategy-panel";
import {
  controlProjection,
  driverName,
  intelligenceProjection,
  strategyFrame,
  strategyPayload,
  strategySituation,
} from "@/test/strategy-fixtures";
import type {
  ControlProjection,
  IntelligenceProjection,
  RaceRoomMode,
  StrategyFrame,
} from "@/lib/types";

type PanelOptions = {
  mode?: RaceRoomMode;
  frame?: StrategyFrame | null;
  projection?: IntelligenceProjection | null;
  control?: ControlProjection | null;
};

function renderPanel(options: PanelOptions = {}) {
  return render(
    <StrategyPanel
      mode={options.mode ?? "FAN"}
      frame={options.frame === undefined ? strategyFrame() : options.frame}
      projection={options.projection === undefined ? intelligenceProjection() : options.projection}
      control={options.control === undefined ? controlProjection() : options.control}
      driverName={driverName}
    />,
  );
}

function panel(): HTMLElement {
  return screen.getByRole("region", { name: /The strategy story|Strategy frame/ });
}

describe("StrategyPanel", () => {
  describe("populated frame", () => {
    it("tells the fan story in plain language", () => {
      renderPanel({ mode: "FAN" });

      expect(screen.getByRole("heading", { name: "The strategy story" })).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "What is happening?" })).toBeInTheDocument();
      expect(
        screen.getByText(
          "Lando Norris is in position to try an undercut on Charles Leclerc by stopping first.",
        ),
      ).toBeInTheDocument();
      expect(
        screen.getByText(/needs to find about 1\.284s before Charles Leclerc answers/),
      ).toBeInTheDocument();
      expect(panel()).toHaveAttribute("data-mode", "fan");
    });

    it("exposes the same frame in full for an analyst", () => {
      renderPanel({ mode: "ANALYST" });

      expect(screen.getByRole("heading", { name: "Strategy frame" })).toBeInTheDocument();
      expect(screen.getByRole("article", { name: "Undercut condition" })).toBeInTheDocument();
      expect(
        screen.getByRole("heading", { name: "What the system can determine" }),
      ).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "Data quality" })).toBeInTheDocument();
      expect(panel()).toHaveAttribute("data-mode", "analyst");
    });

    it("keeps fan mode free of raw payload dumps", () => {
      renderPanel({ mode: "FAN" });
      expect(screen.queryByText("undercut_condition")).toBeNull();
      expect(screen.queryByRole("heading", { name: "Data quality" })).toBeNull();
    });
  });

  describe("null frame", () => {
    it("explains the absence instead of leaving an empty card", () => {
      renderPanel({ frame: null, projection: intelligenceProjection({ status: "pending" }) });

      expect(
        screen.getByText("Strategy analysis is not available for this view yet."),
      ).toBeInTheDocument();
      expect(screen.getByText(/still being computed for this point/)).toBeInTheDocument();
      expect(screen.getByText(/Nothing is inferred in place of the missing analysis/)).toBeInTheDocument();
    });

    it("explains a stale cursor as a deliberate hold-back", () => {
      renderPanel({ frame: null, projection: intelligenceProjection({ status: "stale" }) });
      expect(screen.getByText(/held back rather than guessed/)).toBeInTheDocument();
    });

    it("falls back to an honest unknown when no projection is attached", () => {
      renderPanel({ frame: null, projection: null });
      expect(screen.getByText(/has not reported a status for this session yet/)).toBeInTheDocument();
    });

    it("shows the same notice in analyst mode", () => {
      renderPanel({ mode: "ANALYST", frame: null });
      expect(
        screen.getByText("Strategy analysis is not available for this view yet."),
      ).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "Situations" })).toBeNull();
    });
  });

  describe("frame published as unavailable", () => {
    it("quotes the backend limitation rather than rendering an empty frame", () => {
      renderPanel({
        frame: strategyFrame({
          projection_status: "unavailable",
          situations: [],
          limitations: ["projection_not_current"],
        }),
        projection: intelligenceProjection({ status: "stale", failure_code: "worker_timeout" }),
      });

      expect(screen.getByText("Analysis unavailable")).toBeInTheDocument();
      expect(screen.getByText("Projection not current.")).toBeInTheDocument();
      expect(screen.getByText("Pipeline failure: Worker timeout.")).toBeInTheDocument();
    });
  });

  describe("partial and unavailable capabilities", () => {
    const frame = strategyFrame({
      capabilities: {
        ...strategyFrame().capabilities,
        extra_stop_consequence: {
          availability: "unavailable",
          reason: "missing_authoritative_remaining_distance",
        },
        relative_pace: { availability: "partial", reason: "insufficient_green_samples" },
      },
    });

    it("tells a fan how many checks cannot run without dumping codes", () => {
      renderPanel({ mode: "FAN", frame });
      expect(
        screen.getByText(/1 of the 8 strategy checks cannot run on the data available/),
      ).toBeInTheDocument();
    });

    it("shows an analyst which check is blocked and why", () => {
      renderPanel({ mode: "ANALYST", frame });

      const item = screen.getByText("Extra stop consequence").closest("li");
      expect(item).toHaveAttribute("data-availability", "unavailable");
      expect(within(item as HTMLElement).getByText(/Missing authoritative remaining distance/)).toBeInTheDocument();
      expect(screen.getByText("Relative pace").closest("li")).toHaveAttribute(
        "data-availability",
        "partial",
      );
    });

    it("hedges a partially available situation in fan wording", () => {
      renderPanel({
        mode: "FAN",
        frame: strategyFrame({ situations: [strategySituation({ availability: "partial" })] }),
      });

      const statement = screen.getByText(/may be in position to try an undercut on Charles Leclerc/);
      const item = statement.closest("li");
      expect(item).not.toBeNull();
      expect(within(item as HTMLElement).getByText("Not confirmed")).toBeInTheDocument();
    });
  });

  describe("withdrawn situation", () => {
    const frame = strategyFrame({
      situations: [
        strategySituation({
          status: "withdrawn",
          transition: "withdrawn",
          limitations: ["pit_window_closed"],
        }),
      ],
    });

    it("reads as a revision for a fan, never silently disappearing", () => {
      renderPanel({ mode: "FAN", frame });

      const statement = screen.getByText(/Earlier read:/);
      expect(statement).toHaveTextContent("That no longer holds because pit window closed.");
      expect(statement.closest("li")).toHaveAttribute("data-tone", "revision");
      expect(screen.getByText("No longer holds")).toBeInTheDocument();
    });

    it("stays in the analyst frame with its withdrawal metadata", () => {
      renderPanel({ mode: "ANALYST", frame });

      expect(
        screen.getByRole("article", { name: "Undercut condition, withdrawn" }),
      ).toBeInTheDocument();
      expect(screen.getByText(/Withdrawn at sequence 42/)).toBeInTheDocument();
    });
  });

  describe("truncated control history", () => {
    const control = controlProjection({ history_truncated: true });

    it("warns a fan that clean-lap inference is uncertain", () => {
      renderPanel({ mode: "FAN", control });
      expect(
        screen.getByText(
          "Race control history was truncated, so how many clean laps a car has had since its stop is uncertain.",
        ),
      ).toBeInTheDocument();
    });

    it("records it as a data-quality note for an analyst", () => {
      renderPanel({ mode: "ANALYST", control });
      expect(
        screen.getByText(/Control history is truncated, so clean-lap inference/),
      ).toBeInTheDocument();
    });

    it("mentions it even when no frame was published", () => {
      renderPanel({ frame: null, control });
      expect(
        screen.getByText("Race control history was truncated for this session."),
      ).toBeInTheDocument();
    });
  });

  describe("weather narrative", () => {
    it("describes heavier rain in fan language with no numeric dump", () => {
      renderPanel({
        mode: "FAN",
        frame: strategyFrame({
          situations: [
            strategySituation({
              kind: "weather_change",
              participants: [],
              payload: strategyPayload({
                rainfall_before: false,
                rainfall_now: true,
                track_temperature_change: -2.4,
              }),
            }),
          ],
        }),
      });

      expect(screen.getByText("Rain has started falling.")).toBeInTheDocument();
      expect(screen.queryByText(/-2\.4/)).toBeNull();
    });
  });
});
