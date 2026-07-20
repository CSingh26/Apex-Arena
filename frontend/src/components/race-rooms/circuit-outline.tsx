// SPDX-License-Identifier: AGPL-3.0-only
import Image from "next/image";

import { publicAssetPath } from "@/lib/app-paths";

type CircuitOutlineProps = {
  circuitName: string;
  eventName?: string;
  compact?: boolean;
};

// Layouts are pinned from julesr0y/f1-circuits-svg v2026.2.1 (CC BY 4.0).
// Each identifier below is the repository's layout tagged for the 2026 season.
const CIRCUIT_ASSETS: Array<[RegExp, string]> = [
  [/albert park|melbourne/i, "melbourne-2"],
  [/bahrain|sakhir/i, "bahrain-1"],
  [/shanghai/i, "shanghai-1"],
  [/suzuka/i, "suzuka-2"],
  [/miami/i, "miami-1"],
  [/gilles villeneuve|montreal/i, "montreal-6"],
  [/monaco|monte carlo/i, "monaco-6"],
  [/catalunya|barcelona/i, "catalunya-6"],
  [/spielberg|red bull ring/i, "spielberg-3"],
  [/silverstone/i, "silverstone-8"],
  [/spa|francorchamps/i, "spa-francorchamps-4"],
  [/hungaroring|budapest/i, "hungaroring-3"],
  [/zandvoort/i, "zandvoort-5"],
  [/monza/i, "monza-7"],
  [/madring|madrid/i, "madring-1"],
  [/baku/i, "baku-1"],
  [/marina bay|singapore/i, "marina-bay-4"],
  [/americas|austin|cota/i, "austin-1"],
  [/hermanos rodr.guez|mexico city/i, "mexico-city-3"],
  [/jos. carlos pace|interlagos|s.o paulo/i, "interlagos-2"],
  [/las vegas/i, "las-vegas-1"],
  [/lusail|losail|qatar/i, "lusail-1"],
  [/yas marina|abu dhabi/i, "yas-marina-2"],
  [/jeddah/i, "jeddah-1"],
];

export function circuitAssetId(circuitName: string): string | null {
  return CIRCUIT_ASSETS.find(([pattern]) => pattern.test(circuitName))?.[1] ?? null;
}

export function CircuitOutline({ circuitName, eventName, compact = false }: CircuitOutlineProps) {
  const assetId = circuitAssetId(circuitName);
  const label = `${eventName ?? circuitName} 2026 circuit layout`;
  const className = compact ? "circuit-outline circuit-outline--compact" : "circuit-outline";

  return <div className={className}>
    {assetId ? <div className="circuit-outline__art" role="img" aria-label={label}>
      <Image className="circuit-outline__asset circuit-outline__asset--dark" src={publicAssetPath(`/circuits/2026/white-outline/${assetId}.svg`)} alt="" fill sizes={compact ? "96px" : "210px"} unoptimized />
      <Image className="circuit-outline__asset circuit-outline__asset--light" src={publicAssetPath(`/circuits/2026/black-outline/${assetId}.svg`)} alt="" fill sizes={compact ? "96px" : "210px"} unoptimized />
    </div> : <div className="circuit-outline__unavailable" role="img" aria-label={`${label} unavailable`}>Layout unavailable</div>}
    {!compact && <span title="Circuit artwork by Jules Roy, CC BY 4.0"><i /> 2026 circuit trace</span>}
  </div>;
}
