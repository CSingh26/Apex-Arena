// SPDX-License-Identifier: AGPL-3.0-only

type CircuitOutlineProps = {
  circuitName: string;
  eventName?: string;
  compact?: boolean;
};

const CIRCUIT_PATHS: Array<[RegExp, string]> = [
  [/spa|francorchamps/i, "M20 82 C31 69 41 49 51 25 C57 12 68 15 72 29 L82 58 C86 68 77 77 65 70 L49 60 C41 55 34 64 37 73 C41 85 31 93 20 82 Z"],
  [/silverstone/i, "M15 58 L31 42 L40 18 L51 27 L65 22 L82 31 L72 47 L87 60 L73 82 L56 73 L43 87 L25 80 L31 65 Z"],
  [/monza/i, "M20 77 L29 22 C31 14 42 12 47 20 L54 35 L79 27 C88 25 91 37 84 43 L65 58 L79 77 C84 84 76 91 68 87 L49 77 L32 88 C25 92 18 85 20 77 Z"],
  [/monaco/i, "M17 73 C22 56 22 35 38 25 C50 17 62 20 68 29 C75 39 64 46 54 42 C46 39 41 47 47 55 L74 75 C82 82 75 91 65 87 L44 78 C35 74 29 87 20 83 C16 81 15 77 17 73 Z"],
  [/suzuka/i, "M17 65 C25 45 41 27 61 24 C79 21 88 36 78 48 C68 60 48 47 40 57 C31 68 51 83 68 75 C78 70 87 78 80 86 C70 96 50 91 37 82 L17 68 Z"],
  [/miami/i, "M16 73 L24 25 L41 18 L52 35 L78 26 L86 42 L70 54 L84 76 L69 87 L49 72 L29 87 Z"],
  [/hungaroring/i, "M19 69 C20 42 34 21 58 20 C78 20 88 36 78 49 C70 59 55 49 47 59 C39 69 55 78 70 73 C82 69 88 81 78 88 C60 99 36 89 19 73 Z"],
];

const DEFAULT_PATH = "M16 67 C22 38 40 18 65 22 C84 25 90 43 76 55 L63 66 L81 78 C88 84 81 92 72 88 L48 78 L30 88 C21 93 13 84 16 67 Z";

export function circuitPath(circuitName: string): string {
  return CIRCUIT_PATHS.find(([pattern]) => pattern.test(circuitName))?.[1] ?? DEFAULT_PATH;
}

export function CircuitOutline({ circuitName, eventName, compact = false }: CircuitOutlineProps) {
  const label = `${eventName ?? circuitName} circuit outline`;
  return <div className={compact ? "circuit-outline circuit-outline--compact" : "circuit-outline"}>
    <svg viewBox="0 0 100 100" role="img" aria-label={label}>
      <path className="circuit-outline__shadow" d={circuitPath(circuitName)} pathLength="100" />
      <path className="circuit-outline__track" d={circuitPath(circuitName)} pathLength="100" />
      <circle className="circuit-outline__start" cx="20" cy="82" r="3" />
    </svg>
    {!compact && <span><i /> Circuit trace</span>}
  </div>;
}
