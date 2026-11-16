import { useEffect, useState } from "react";

import { api } from "../api";

/** The screen that describes the system: what Markwright is, how a result is
 * produced, the honesty rules, and the live state of THIS deployment. */
export default function Overview({ onNavigate }: { onNavigate: (s: string) => void }) {
  const [ready, setReady] = useState<{ profile: string; schemes: number; calibrated_configs: number } | null>(null);
  const [policy, setPolicy] = useState<{ fpr_target: number; retention_days: number } | null>(null);
  const [counts, setCounts] = useState<{ models: number; configs: number } | null>(null);

  useEffect(() => {
    api.readyz().then(setReady).catch(() => setReady(null));
    api.policy().then(setPolicy).catch(() => setPolicy(null));
    Promise.all([api.models(), api.configs()])
      .then(([m, c]) => setCounts({ models: m.length, configs: c.length }))
      .catch(() => setCounts(null));
  }, []);

  return (
    <>
      <div className="card">
        <h2>What this is</h2>
        <p>
          Markwright is a control plane for LLM text watermarking. It keeps a versioned registry
          binding each model version to a published watermarking scheme and its exact parameters,
          holds the generation keys behind a vault boundary with rotation and revocation, and
          verifies text — returning <b>calibrated statistical evidence</b>, never an “AI: yes/no”
          verdict. It wraps published schemes (SynthID-Text, KGW) and invents no algorithm and no
          cryptography.
        </p>
        <p className="hint">
          Evidence is an ordered single-hue scale — intensity, not judgment:
        </p>
        <div className="evidence-scale" title="no/insufficient · weak · moderate · strong">
          <span /><span /><span /><span />
        </div>
        <div className="row hint" style={{ justifyContent: "space-between", marginTop: 2 }}>
          <span>no / insufficient</span><span>weak</span><span>moderate</span><span>strong</span>
        </div>
      </div>

      <div className="card">
        <h2>How a result is produced</h2>
        <div className="pipeline">
          {["gate", "candidates", "detect", "calibrate", "Bonferroni", "band"].map((s, i) => (
            <span key={s} style={{ display: "contents" }}>
              {i > 0 && <span className="arrow">→</span>}
              <span className="step">{s}</span>
            </span>
          ))}
        </div>
        <p className="hint">
          Short or low-entropy text is <i>gated</i> before any statistic exists. Every candidate
          configuration is scored by its scheme adapter, mapped through a null model fitted on
          human text (empirical CDF + generalised-Pareto tail), corrected for how many configs were
          scanned, and only then banded against the deployment’s false-positive target. A
          calibration that cannot resolve that target caps the band and says so.
        </p>
      </div>

      <div className="grid2">
        <div className="card">
          <h2>Honesty rules (enforced in code)</h2>
          <div className="rule"><b>No booleans.</b> There is no is_ai field anywhere; bool(result) raises.</div>
          <div className="rule"><b>The gate precedes the statistic.</b> Insufficient evidence has no p-value, ever.</div>
          <div className="rule"><b>Intervals always.</b> A p-value is never shown without its 95% interval and FPR target.</div>
          <div className="rule"><b>Caveats cannot be collapsed.</b> Including: no evidence is not evidence of human authorship.</div>
          <div className="rule"><b>Text is never stored.</b> Salted digest and statistics only.</div>
          <div className="rule"><b>Keys never leave the backend.</b> The registry stores handles; old keys verify forever.</div>
        </div>
        <div className="card">
          <h2>This deployment</h2>
          <dl className="facts">
            <dt>profile</dt><dd>{ready?.profile ?? "–"}</dd>
            <dt>schemes installed</dt><dd>{ready?.schemes ?? "–"}</dd>
            <dt>models / configs</dt><dd>{counts ? `${counts.models} / ${counts.configs}` : "–"}</dd>
            <dt>calibrated configs</dt><dd>{ready?.calibrated_configs ?? "–"}</dd>
            <dt>fpr target</dt><dd>{policy ? policy.fpr_target.toExponential(1) : "–"}</dd>
            <dt>result retention</dt><dd>{policy ? `${policy.retention_days} days (metadata only)` : "–"}</dd>
          </dl>
          <div className="row">
            <button onClick={() => onNavigate("verify")}>Verify a text</button>
            <button className="ghost" onClick={() => onNavigate("onboard")}>Onboard a model</button>
          </div>
        </div>
      </div>
    </>
  );
}
