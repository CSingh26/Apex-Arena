// SPDX-License-Identifier: AGPL-3.0-only
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RoomContext } from "@/components/race-rooms/room-context";
import { detail, playback } from "@/test/race-room-fixtures";

describe("RoomContext", () => {
  it("shows circuit records, facts, and every OpenF1 weather measurement", () => {
    render(<RoomContext slug={detail.room.slug} detail={detail} playback={playback} />);

    expect(screen.getByRole("heading", { name: "Track dossier" })).toBeInTheDocument();
    expect(screen.getByText("5.891 km")).toBeInTheDocument();
    expect(screen.getByText("Max Verstappen · 2020")).toBeInTheDocument();
    expect(screen.getByText(/airfield perimeter road/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Official circuit guide/i })).toHaveAttribute("target", "_blank");

    expect(screen.getByRole("heading", { name: "Track weather" })).toBeInTheDocument();
    expect(screen.getByText("22.5°C")).toBeInTheDocument();
    expect(screen.getByText("34.1°C")).toBeInTheDocument();
    expect(screen.getByText("None")).toBeInTheDocument();
    expect(screen.getByText("71%")).toBeInTheDocument();
    expect(screen.getByText("1,008.2 mbar")).toBeInTheDocument();
    expect(screen.getByText("3.4 m/s")).toBeInTheDocument();
    expect(screen.getByText("SW · 247°")).toBeInTheDocument();
  });

  it("keeps a useful weather panel before OpenF1 publishes a session", () => {
    const pending = {
      ...detail,
      weather: {
        ...detail.weather,
        available: false,
        sampled_at: null,
        notice: "Weather will appear when OpenF1 publishes this session.",
      },
    };

    render(<RoomContext slug={detail.room.slug} detail={pending} playback={playback} />);

    expect(screen.getByRole("heading", { name: "Track weather" })).toBeInTheDocument();
    expect(screen.getByText(/Weather will appear/i)).toBeInTheDocument();
    expect(screen.queryByText("Air")).not.toBeInTheDocument();
  });
});
