import { useEffect, useState } from "react";

import { api, fmtFpr } from "../api";

/** The screen that describes the system: what Markwright is, how a result is
 * produced, the honesty rules, and the live state of THIS deployment. */
export default function Overview({ onNavigate, role }: { onNavigate: (s: string) => void; role?: string }) {
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
          that binds each model version to a published watermarking scheme and its exact
          parameters, holds the generation keys behind a vault boundary with rotation and
          revocation, and verifies text. A verification returns <b>calibrated statistical
          evidence</b>; deciding whether a text was written by AI is outside what any watermark
          check can do. The schemes themselves are published work (SynthID-Text, KGW); Markwright
          adds no algorithm and no cryptography of its own.
        </p>
        <p className="hint">
          Every result lands on this scale. Colour shows how strong the evidence is; it never
          means pass or fail:
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
          {["length check", "candidates", "detect", "calibrate", "multi-scan correction", "band"].map((s, i) => (
            <span key={s} style={{ display: "contents" }}>
              {i > 0 && <span className="arrow">→</span>}
              <span className="step">{s}</span>
            </span>
          ))}
        </div>
        <p className="hint">
          Text that is too short or too predictable is rejected before any statistic is computed.
          Each candidate configuration then scores the text with its scheme, the score is mapped
          through a null model fitted on human text, the p-value is corrected for how many
          configurations were scanned, and the corrected value is banded against the deployment's
          false-positive target. When the calibration cannot resolve that target, the band is
          capped and the result says why.
        </p>
      </div>

      <div className="grid2">
        <div className="card">
          <h2>Reporting rules (each has a test)</h2>
          <div className="rule"><b>No yes/no answers.</b> There is no is_ai field anywhere, and code that treats a result as true/false raises an error.</div>
          <div className="rule"><b>Short text is rejected first.</b> An insufficient-evidence result has no p-value at all.</div>
          <div className="rule"><b>Intervals always.</b> A p-value always appears with its 95% interval and the false-positive target.</div>
          <div className="rule"><b>Caveats always show.</b> Every no-evidence result states that absence of a mark says nothing about human authorship.</div>
          <div className="rule"><b>Text is never stored.</b> Only a salted digest and the statistics.</div>
          <div className="rule"><b>Keys stay in the backend.</b> The registry stores references; rotated keys keep verifying old text.</div>
        </div>
        <div className="card">
          <h2>This deployment</h2>
          <dl className="facts">
            <dt>profile</dt><dd>{ready?.profile ?? "–"}</dd>
            <dt>schemes installed</dt><dd>{ready?.schemes ?? "–"}</dd>
            <dt>models / configs</dt><dd>{counts ? `${counts.models} / ${counts.configs}` : "–"}</dd>
            <dt>calibrated configs</dt><dd>{ready?.calibrated_configs ?? "–"}</dd>
            <dt>false-positive target</dt><dd>{policy ? `${fmtFpr(policy.fpr_target)} (${policy.fpr_target.toExponential(1)})` : "–"}</dd>
            <dt>result retention</dt><dd>{policy ? `${policy.retention_days} days (metadata only)` : "–"}</dd>
          </dl>
          <dl className="facts" style={{ marginTop: 0 }}>
            <dt>your role</dt><dd>{role ?? "–"}</dd>
          </dl>
          <div className="row">
            <button onClick={() => onNavigate("verify")}>Verify a text</button>
            <button className="ghost" onClick={() => onNavigate("schemes")}>How schemes work</button>
            <button className="ghost" onClick={() => onNavigate("onboard")}>Onboard a model</button>
          </div>
        </div>
      </div>
    </>
  );
}
