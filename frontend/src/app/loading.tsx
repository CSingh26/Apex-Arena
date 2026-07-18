// SPDX-License-Identifier: AGPL-3.0-only
import { ApexRaceLoader } from "@/components/loading/apex-race-loader";

export default function Loading() {
  return <main className="route-loading track-grid"><ApexRaceLoader label="Building the race signal" /></main>;
}
