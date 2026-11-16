import { useEffect, useState } from "react";

import { api, ApiError, DemoSample, fmtFpr, VerificationResult } from "../api";
import { ErrorBanner } from "../components";

// Full caveat sentences, shown verbatim and always.
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
  const ci = best?.ci_95;   // every p-value shows its interval
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
              {res.candidates.length > 1
                ? `, corrected for ${res.candidates.length} configurations scanned`
                : ""}
            </dd>
            <dt>matched configuration</dt>
            <dd>{best.config_id.slice(0, 8)}… ({best.scheme_id}, key v{best.key_version})</dd>
          </>
        )}
        {/* shown on every result, matched or not */}
        <dt>false-positive target</dt>
        <dd>{fmtFpr(res.fpr_target)} ({sci(res.fpr_target)}), set by the deployment</dd>
        <dt>tokens</dt>
        <dd>
          {res.token_count} total, {res.scored_token_count} scored
          {best?.min_detectable_tokens ? ` (minimum detectable: ${best.min_detectable_tokens})` : ""}
        </dd>
        <dt>engine</dt>
        <dd>markwright {res.engine.markwright_version} ({res.engine.mode})</dd>
      </dl>
      {res.candidates.length > 0 && (
        <table style={{ marginTop: "1rem" }}>
          <thead>
            <tr>
              <th>configuration</th><th>scheme</th><th>statistic</th>
              <th>p-value</th><th>corrected</th>
            </tr>
          </thead>
          <tbody>
            {res.candidates.map((c) => (
              <tr key={c.config_id}>
                <td>{c.config_id.slice(0, 8)}…</td>
                <td>{c.scheme_id} (key v{c.key_version})</td>
                <td>{c.statistic.name} = {c.statistic.value.toFixed(3)}</td>
                <td>{sci(c.p_value)}</td>
                <td>{sci(c.p_value_adjusted)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {/* plain elements, always in flow: nothing here can be collapsed */}
      <div className="caveats">
        <h3>Caveats, always shown</h3>
        <ul>{res.caveats.map((c) => <li key={c}>{CAVEAT_TEXT[c] ?? c}</li>)}</ul>
      </div>
    </div>
  );
}

export default function Verify() {
  const [mode, setMode] = useState<"text" | "ids">("text");
  const [input, setInput] = useState("");
  const [fpr, setFpr] = useState("");
  const [result, setResult] = useState<VerificationResult | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [samples, setSamples] = useState<DemoSample[]>([]);
  const [sampleNotes, setSampleNotes] = useState<string[]>([]);
  const [activeSample, setActive] = useState<string | null>(null);

  useEffect(() => {
    api.samples()
      .then((s) => { setSamples(s.samples); setSampleNotes(s.notes); })
      .catch(() => setSamples([]));
  }, []);

  const approxTokens = mode === "text"
    ? Math.round(input.trim().split(/\s+/).filter(Boolean).length * 1.3)
    : (() => { try { return (JSON.parse(input) as number[]).length; } catch { return 0; } })();

  function loadSample(s: DemoSample) {
    setMode("ids");
    setInput(JSON.stringify(s.token_ids));
    setActive(s.id);
    setResult(null);
    setError(null);
  }

  async function run() {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const fprVal = fpr ? Number(fpr) : undefined;
      setResult(mode === "text"
        ? await api.verifyText(input, fprVal)
        : await api.verifyIds(JSON.parse(input) as number[], fprVal));
    } catch (e) {
      setError(e instanceof SyntaxError
        ? new Error("That is not a JSON array of token ids. Example: [312, 9981, 44]")
        : e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {samples.length > 0 && (
        <div className="card">
          <h2>Try an example</h2>
          <p className="hint">
            Generated on this deployment, so these are real inputs rather than canned answers.
          </p>
          <div className="row">
            {samples.map((s) => (
              <button key={s.id} className={activeSample === s.id ? "" : "ghost"} onClick={() => loadSample(s)}>
                {s.label}
              </button>
            ))}
          </div>
          {activeSample && (
            <p className="hint" style={{ marginTop: "0.6rem" }}>
              {samples.find((s) => s.id === activeSample)?.why} Expect:{" "}<b>{samples.find((s) => s.id === activeSample)?.expect}</b>.
            </p>
          )}
          {sampleNotes.map((n) => <p key={n} className="hint">· {n}</p>)}
        </div>
      )}

      <div className="card">
        <textarea
          value={input}
          onChange={(e) => { setInput(e.target.value); setActive(null); }}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && input.trim() && !busy) void run();
          }}
          placeholder={
            mode === "text"
              ? "Paste the text to verify. It is never stored; the server keeps only a salted digest and the statistics."
              : "Paste token ids as a JSON array, for example [312, 9981, 44]. This path needs no tokenizer."
          }
        />
        <div className="row">
          <label>
            <input type="radio" checked={mode === "text"} onChange={() => setMode("text")} /> text
          </label>
          <label>
            <input type="radio" checked={mode === "ids"} onChange={() => setMode("ids")} /> token ids
          </label>
          <label>
            false-positive target{" "}
            <input style={{ width: "6rem", font: "inherit" }} placeholder="deployment value"
              value={fpr} onChange={(e) => setFpr(e.target.value)} />{" "}
            <span className="hint">(can only be lowered)</span>
          </label>
          <button onClick={run} disabled={busy || !input.trim()}>
            {busy ? "verifying…" : "Verify"}
          </button>
          <span className="hint">
            {approxTokens > 0
              ? `${approxTokens}${mode === "text" ? " tokens (estimated)" : " token ids"}` +
                (approxTokens < 50 ? " — below the usual minimum, so expect insufficient evidence" : "")
              : "⌘/Ctrl + Enter to verify"}
          </span>
        </div>
      </div>

      {error != null && <div className="card"><ErrorBanner error={error as ApiError} /></div>}
      {result && <ResultCard res={result} />}
    </>
  );
}
