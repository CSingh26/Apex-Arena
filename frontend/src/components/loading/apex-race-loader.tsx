// SPDX-License-Identifier: AGPL-3.0-only

type ApexRaceLoaderProps = {
  label?: string;
  compact?: boolean;
};

export function ApexRaceLoader({ label = "Loading race signal", compact = false }: ApexRaceLoaderProps) {
  return <div className={compact ? "apex-race-loader apex-race-loader--compact" : "apex-race-loader"} role="status" aria-label={label}>
    <div className="apex-race-loader__stage" aria-hidden>
      <svg viewBox="0 0 180 96">
        <path className="apex-race-loader__track-shadow" d="M18 62 C25 30 51 15 84 22 C105 27 113 47 99 58 L88 66 L126 78 C145 84 162 70 154 51 C148 35 132 28 119 35" pathLength="100" />
        <path className="apex-race-loader__track" d="M18 62 C25 30 51 15 84 22 C105 27 113 47 99 58 L88 66 L126 78 C145 84 162 70 154 51 C148 35 132 28 119 35" pathLength="100" />
        <g className="apex-race-loader__mark">
          <path d="M54 43 H94 L87 52 H61 Z" />
          <path d="M72 55 H87 L77 68 H62 Z" />
          <path d="M96 43 H113 L105 52 H91 Z" />
        </g>
        <circle className="apex-race-loader__signal" cx="18" cy="62" r="3" />
      </svg>
      <span className="apex-race-loader__wordmark">APEX <b>ARENA</b></span>
    </div>
    <p>{label}<span aria-hidden>…</span></p>
  </div>;
}
