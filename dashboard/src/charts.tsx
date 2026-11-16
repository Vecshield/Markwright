import { Band, fmtFpr } from "./api";

/* Visual vocabulary for evidence. Every chart here obeys the §8.3 rules:
   one hue at varying intensity, no red/green, and no shape that implies
   pass/fail. They show magnitude and uncertainty, which the numbers alone
   make hard to feel. */

const BAND_ORDER: Band[] = [
  "insufficient_evidence",
  "no_evidence",
  "weak_evidence",
  "moderate_evidence",
  "strong_evidence",
];
const BAND_LABEL: Record<Band, string> = {
  insufficient_evidence: "insufficient",
  no_evidence: "none",
  weak_evidence: "weak",
  moderate_evidence: "moderate",
  strong_evidence: "strong",
};

/** Where this result sits on the evidence scale. "Insufficient" is drawn apart
 * from the ordered part, because it is the absence of a measurement rather
 * than a low one. */
export function EvidenceScale({ band }: { band: Band }) {
  const ordered = BAND_ORDER.slice(1);
  const gapX = 128;
  const stepW = 92;
  const w = gapX + ordered.length * stepW;
  const insufficient = band === "insufficient_evidence";
  return (
    <svg viewBox={`0 0 ${w} 62`} className="chart" role="img"
      aria-label={`evidence level: ${BAND_LABEL[band]}`}>
      <rect x={0} y={16} width={104} height={20} rx={5}
        className={`sc-seg sc-ins ${insufficient ? "sc-on" : ""}`} />
      <text x={52} y={50} className={`sc-lab ${insufficient ? "sc-lab-on" : ""}`}>
        not measured
      </text>
      {ordered.map((b, i) => {
        const x = gapX + i * stepW;
        return (
          <g key={b}>
            <rect x={x} y={16} width={stepW - 8} height={20} rx={5}
              className={`sc-seg sc-${i} ${band === b ? "sc-on" : ""}`} />
            <text x={x + (stepW - 8) / 2} y={50}
              className={`sc-lab ${band === b ? "sc-lab-on" : ""}`}>
              {BAND_LABEL[b]}
            </text>
          </g>
        );
      })}
      {/* marker above the active segment */}
      {(() => {
        const i = ordered.indexOf(band);
        const cx = insufficient ? 52 : gapX + i * stepW + (stepW - 8) / 2;
        return <path d={`M ${cx - 6} 8 L ${cx + 6} 8 L ${cx} 14 z`} className="sc-marker" />;
      })()}
    </svg>
  );
}

/** p-value against the deployment's target, on a log axis, with the 95%
 * interval drawn as a band. Strength reads as distance left of the target. */
export function PValueAxis({ p, ci, target }: {
  p: number; ci: [number, number] | null; target: number;
}) {
  const w = 620;
  const lo = Math.min(1e-8, p / 10, ci ? ci[0] / 10 : 1, target / 10);
  const pos = (v: number) => {
    const t = (Math.log10(Math.max(v, lo)) - Math.log10(lo)) / (0 - Math.log10(lo));
    return 24 + t * (w - 48);
  };
  const decades: number[] = [];
  for (let e = Math.ceil(Math.log10(lo)); e <= 0; e++) decades.push(10 ** e);
  return (
    <svg viewBox={`0 0 ${w} 86`} className="chart" role="img"
      aria-label={`p-value ${p.toExponential(1)} against a target of ${target.toExponential(1)}`}>
      <line x1={24} y1={52} x2={w - 24} y2={52} className="ax-line" />
      {decades.map((d) => (
        <g key={d}>
          <line x1={pos(d)} y1={48} x2={pos(d)} y2={56} className="ax-tick" />
          <text x={pos(d)} y={70} className="ax-tick-label">1e{Math.round(Math.log10(d))}</text>
        </g>
      ))}
      {/* the deployment's bar */}
      <line x1={pos(target)} y1={22} x2={pos(target)} y2={60} className="ax-target" />
      <text x={pos(target)} y={16} className="ax-target-label">
        target {fmtFpr(target)}
      </text>
      {/* uncertainty, then the point */}
      {ci && (
        <rect x={Math.min(pos(ci[0]), pos(ci[1]))} y={44}
          width={Math.max(2, Math.abs(pos(ci[1]) - pos(ci[0])))} height={16} rx={3}
          className="ax-ci" />
      )}
      <circle cx={pos(p)} cy={52} r={6} className="ax-point" />
      <text x={pos(p)} y={84} className="ax-point-label">
        p {p.toExponential(1)}
      </text>
    </svg>
  );
}

/** Tokens scored against the length this configuration needs. */
export function TokenGauge({ scored, total, minimum }: {
  scored: number; total: number; minimum: number | null;
}) {
  const w = 620;
  const cap = Math.max(total, minimum ?? 0) * 1.12 || 1;
  const px = (n: number) => 8 + (n / cap) * (w - 16);
  return (
    <svg viewBox={`0 0 ${w} 56`} className="chart" role="img"
      aria-label={`${scored} of ${total} tokens scored${minimum ? `, minimum ${minimum}` : ""}`}>
      <rect x={8} y={16} width={px(total) - 8} height={18} rx={5} className="tg-total" />
      <rect x={8} y={16} width={px(scored) - 8} height={18} rx={5} className="tg-scored" />
      <text x={px(total) + 8} y={30} className="tg-label">{total} tokens, {scored} scored</text>
      {minimum != null && (
        <>
          <line x1={px(minimum)} y1={10} x2={px(minimum)} y2={40} className="tg-min" />
          <text x={px(minimum)} y={52} className="tg-min-label">minimum {minimum}</text>
        </>
      )}
    </svg>
  );
}

/** Every configuration scanned, ordered by how far its corrected p-value sits
 * below the target. Makes "which one matched, and by how much" immediate. */
export function CandidateChart({ rows, target, best }: {
  rows: { config_id: string; scheme_id: string; p_value_adjusted: number }[];
  target: number;
  best: string | null;
}) {
  const w = 620;
  const rowH = 26;
  const lo = Math.min(1e-8, ...rows.map((r) => r.p_value_adjusted / 10), target / 10);
  const pos = (v: number) => {
    const t = (Math.log10(Math.max(v, lo)) - Math.log10(lo)) / (0 - Math.log10(lo));
    return 150 + t * (w - 174);
  };
  return (
    <svg viewBox={`0 0 ${w} ${rows.length * rowH + 26}`} className="chart" role="img"
      aria-label="corrected p-value per configuration scanned">
      <line x1={pos(target)} y1={4} x2={pos(target)} y2={rows.length * rowH + 8}
        className="ax-target" />
      <text x={pos(target)} y={rows.length * rowH + 22} className="ax-target-label">
        target
      </text>
      {rows.map((r, i) => {
        const y = 10 + i * rowH;
        const matched = r.config_id === best;
        return (
          <g key={r.config_id}>
            <text x={0} y={y + 13} className="cb-name">
              {r.scheme_id}
            </text>
            <line x1={150} y1={y + 9} x2={w - 24} y2={y + 9} className="cb-track" />
            <circle cx={pos(r.p_value_adjusted)} cy={y + 9} r={matched ? 6 : 4.5}
              className={matched ? "cb-dot cb-dot-on" : "cb-dot"} />
          </g>
        );
      })}
    </svg>
  );
}
