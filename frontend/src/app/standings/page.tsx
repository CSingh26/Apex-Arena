// SPDX-License-Identifier: AGPL-3.0-only
import type { Metadata } from "next";

import { StandingsPage } from "@/components/standings/standings-page";

export const metadata: Metadata = {
  title: "2026 Championship Standings — Apex Arena",
  description: "Live and current 2026 Formula racing driver and constructor championship standings.",
};

export default function ChampionshipStandingsPage() {
  return <StandingsPage />;
}
