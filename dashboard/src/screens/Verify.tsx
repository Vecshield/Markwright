import { useState } from "react";

import { api, VerificationResult } from "../api";

// Full caveat sentences (same texts the CLI prints) — shown verbatim, always.
const CAVEAT_TEXT: Record<string, string> = {
  result_is_probabilistic: "statistical evidence; some human texts score this high by chance, at the rate shown",
  short_text: "the text is short, so the evidence is limited",
  low_entropy: "this text leaves the model little free choice (code, lists, quotes), so marks are weak in it",
  possible_paraphrase: "paraphrase or editing can remove marks; a positive may cover only part of the text",
  multiple_candidates: "several configurations were scanned and the p-value was corrected for that",
  historical_key: "matched a rotated or revoked key",
  spoofing_not_excluded: "this configuration's detector is public, so the mark could have been forged",
  tokenizer_uncertain: "tokenizer unavailable or mismatched; this configuration was not scored",
  provisional_calibration: "calibrated on a small starter corpus; evidence bands are capped at its resolution",
};

function sci(x: number): string {
  return x.toExponential(1).replace("e-", " × 10⁻").replace("e+", " × 10");
}

function ResultCard({ res }: { res: VerificationResult }) {
  const best = res.candidates.find((c) => c.config_id === res.best_match) ?? null;
  // every p-value shows its interval — a reader never sees a lone point estimate
  const ci = best?.ci_95;
  return (
    <div className="card" data-testid="result-card">
      <span className={`band band-${res.band}`}>{res.band.replace(/_/g, " ").toUpperCase()}</span>
      <p className="band-reason">{res.band_reason}</p>
      <dl className="facts">
        {best && (
          <>
            <dt>calibrated p-value</dt>
            <dd>
              {sci(best.p_value_adjusted)}
              {ci ? ` (95% interval ${sci(ci[0])} to ${sci(ci[1])})` : ""}
              {res.candidates.length > 1 ? `, adjusted for ${res.candidates.length} configs scanned` : ""}
            </dd>
            <dt>matched config</dt>
            <dd>
              {best.config_id.slice(0, 8)}… ({best.scheme_id}, key v{best.key_version})
            </dd>
          </>
        )}
        {/* the FPR target is rendered on EVERY result, matched or not */}
        <dt>false-positive target</dt>
        <dd>{sci(res.fpr_target)} (deployment policy)</dd>
        <dt>tokens</dt>
        <dd>
          {res.token_count} total, {res.scored_token_count} scored
          {best?.min_detectable_tokens ? ` (minimum detectable: ${best.min_detectable_tokens})` : ""}
        </dd>
        <dt>engine</dt>
        <dd>
          markwright {res.engine.markwright_version} ({res.engine.mode})
        </dd>
      </dl>
      {/* per-candidate table: every scanned config, its statistic and both p-values */}
      {res.candidates.length > 0 && (
        <table style={{ marginTop: "1rem" }}>
          <thead>
            <tr>
              <th>config</th>
              <th>scheme</th>
              <th>statistic</th>
              <th>p</th>
              <th>p adjusted</th>
            </tr>
          </thead>
          <tbody>
            {res.candidates.map((c) => (
              <tr key={c.config_id}>
                <td>{c.config_id.slice(0, 8)}…</td>
                <td>{c.scheme_id} (key v{c.key_version})</td>
                <td>
                  {c.statistic.name} = {c.statistic.value.toFixed(3)}
                </td>
                <td>{sci(c.p_value)}</td>
                <td>{sci(c.p_value_adjusted)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {/* always rendered, never collapsible (design §8.3) */}
      <div className="caveats">
        <h3>Caveats, always shown</h3>
        <ul>
          {res.caveats.map((c) => (
            <li key={c}>{CAVEAT_TEXT[c] ?? c}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export default function Verify() {
  const [mode, setMode] = useState<"text" | "ids">("text");
  const [input, setInput] = useState("");
  const [fpr, setFpr] = useState("");
  const [result, setResult] = useState<VerificationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const fprVal = fpr ? Number(fpr) : undefined;
      const res =
        mode === "text"
          ? await api.verifyText(input, fprVal)
          : await api.verifyIds(JSON.parse(input) as number[], fprVal);
      setResult(res);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="card">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={
            mode === "text"
              ? "Paste the text to verify. It is never stored; the server keeps only a salted digest and the statistics."
              : "Paste token ids as a JSON array, for example [312, 9981, ...]; this path needs no tokenizer"
          }
        />
        <div className="row">
          <label>
            <input type="radio" checked={mode === "text"} onChange={() => setMode("text")} /> text
          </label>
          <label>
            <input type="radio" checked={mode === "ids"} onChange={() => setMode("ids")} /> token ids (JSON)
          </label>
          <label>
            false-positive target{" "}
            <input
              style={{ width: "6rem", font: "inherit" }}
              placeholder="deployment value"
              value={fpr}
              onChange={(e) => setFpr(e.target.value)}
            />{" "}
            <span className="hint">(can only lower the deployment value)</span>
          </label>
          <button onClick={run} disabled={busy || !input.trim()}>
            {busy ? "verifying…" : "Verify"}
          </button>
        </div>
      </div>
      {error && (
        <div className="card">
          <div className="error">{error}</div>
        </div>
      )}
      {result && <ResultCard res={result} />}
    </>
  );
}
