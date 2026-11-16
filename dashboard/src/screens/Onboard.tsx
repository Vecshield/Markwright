import { useEffect, useState } from "react";

import { api, ApiError, CalibrationRun, Config, SchemeInfo } from "../api";

/** The 5-step wizard (design §8.3): register → scheme → config+key →
 * calibrate → activate. Each step is one API call; the wizard never hides
 * what it did — every completed step prints the artifact it produced. */
export default function Onboard({ role }: { role: string }) {
  const [step, setStep] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [modelId, setModelId] = useState("");
  const [version, setVersion] = useState("1.0");
  const [tokenizer, setTokenizer] = useState("gpt2");
  const [schemes, setSchemes] = useState<SchemeInfo[]>([]);
  const [scheme, setScheme] = useState("synthid-text");
  const [cfg, setCfg] = useState<Config | null>(null);
  const [corpora, setCorpora] = useState<string[]>([]);
  const [corpus, setCorpus] = useState<string>("");
  const [run, setRun] = useState<CalibrationRun | null>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    api.schemes().then(setSchemes).catch(() => setSchemes([]));
    api.corpora().then((c) => {
      setCorpora(c);
      if (c.length) setCorpus(c[0]);
    }).catch(() => setCorpora([]));
  }, []);

  const steps = ["register model", "pick scheme", "config + key", "calibrate", "activate"];
  const canOperate = role === "operator" || role === "admin";

  async function guard(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="card">
        <h2>Onboard a model</h2>
        {!canOperate && (
          <p className="hint">
            Onboarding needs the operator or admin role; switch roles in the header. The wizard
            stays visible so you can see what operators do.
          </p>
        )}
        <div className="steps">
          {steps.map((s, i) => (
            <span key={s} className={`step-pill ${i === step ? "now" : i < step ? "done" : ""}`}>
              {i < step ? "✓ " : `${i + 1} · `}{s}
            </span>
          ))}
        </div>
        {error && <div className="error">{error}</div>}

        {step === 0 && (
          <>
            <p className="hint">A model is a registry slug plus a version and its tokenizer. The
              tokenizer matters: a detector running with the wrong one produces confident nonsense,
              so Markwright records a tokenizer fingerprint at calibration and refuses to score on
              a mismatch.</p>
            <div className="row">
              <label>model id <input type="text" placeholder="acme-llm" value={modelId}
                onChange={(e) => setModelId(e.target.value)} /></label>
              <label>version <input type="text" value={version}
                onChange={(e) => setVersion(e.target.value)} /></label>
              <label>tokenizer <input type="text" value={tokenizer}
                onChange={(e) => setTokenizer(e.target.value)} /></label>
              <button disabled={busy || !canOperate || !modelId.trim()} onClick={() => guard(async () => {
                await api.createModel(modelId.trim()).catch((e) => {
                  if (!(e instanceof ApiError && e.status === 409)) throw e;   // exists: fine
                });
                await api.addVersion(modelId.trim(), version.trim(), tokenizer.trim());
                setStep(1);
              })}>Register</button>
            </div>
          </>
        )}

        {step === 1 && (
          <>
            <p className="hint">Each scheme states its trade-offs before you pick it.</p>
            {schemes.map((s) => (
              <div key={s.scheme_id} className="row" style={{ alignItems: "baseline" }}>
                <label>
                  <input type="radio" checked={scheme === s.scheme_id}
                    onChange={() => setScheme(s.scheme_id)} />{" "}
                  <b>{s.scheme_id}</b> <span className="hint">v{s.scheme_version}</span>
                </label>
                <span className="hint">
                  {s.capabilities.distortion_free ? "distortion-free" : "quality cost grows with strength"}
                  {" · "}
                  {s.capabilities.publishable_detector
                    ? "detector publishable (discloses keys!)"
                    : "detector not separable from the key"}
                </span>
              </div>
            ))}
            <div className="row">
              <button className="ghost" onClick={() => setStep(0)}>Back</button>
              <button disabled={busy || !canOperate} onClick={() => guard(async () => {
                const c = await api.createConfig(modelId.trim(), version.trim(), scheme);
                setCfg(c);
                setStep(2);
              })}>Create config</button>
            </div>
          </>
        )}

        {step === 2 && cfg && (
          <>
            <p className="okline">✓ config {cfg.config_id.slice(0, 8)}… created (status: {cfg.status})</p>
            <p className="okline">✓ key lineage {cfg.key_ref?.slice(0, 8)}… created in the key
              backend; the registry stores only a reference to it</p>
            <p className="hint">Next, fit a null model on human text. The server refuses to
              activate a configuration without an accepted calibration.</p>
            <div className="row">
              <button disabled={busy} onClick={() => setStep(3)}>Continue to calibration</button>
            </div>
          </>
        )}

        {step === 3 && cfg && (
          <>
            <p className="hint">
              The bundled starter corpora are small and cover one domain, so the null model they
              produce is <b>provisional</b>: every result will say so, and evidence bands are
              capped at what this calibration can resolve. Production deployments calibrate on
              their own domain corpora.
            </p>
            <div className="row">
              <label>null corpus{" "}
                <select className="field" value={corpus} onChange={(e) => setCorpus(e.target.value)}>
                  {corpora.map((c) => <option key={c}>{c}</option>)}
                </select>
              </label>
              <button disabled={busy || !corpus || !canOperate} onClick={() => guard(async () => {
                const r = await api.calibrate(cfg.config_id, [corpus]);
                setRun(r);
              })}>{busy ? "fitting null model…" : "Run calibration"}</button>
            </div>
            {run && (
              <>
                <p className="okline">✓ run {run.run_id.slice(0, 8)}…: quality {run.quality},{" "}
                  {run.n_docs} documents, smallest resolvable p-value {run.resolution_floor.toExponential(1)}</p>
                {run.notes.map((n) => <p key={n} className="hint">· {n}</p>)}
                <div className="row">
                  <button disabled={busy || !canOperate} onClick={() => guard(async () => {
                    await api.acceptCalibration(run.run_id);
                    setStep(4);
                  })}>Accept calibration</button>
                </div>
              </>
            )}
          </>
        )}

        {step === 4 && cfg && (
          <>
            <p className="hint">Activation starts the configuration marking new text. Deprecating
              it later only stops marking; verification of old text continues indefinitely.</p>
            <div className="row">
              <button disabled={busy || !canOperate || done} onClick={() => guard(async () => {
                await api.transition(cfg.config_id, "activate");
                setDone(true);
              })}>Activate</button>
            </div>
            {done && (
              <>
                <p className="okline">✓ {modelId} {version} is live. It marks text through the
                  generation-profile API and its output can be checked on the Verify screen.</p>
                <p className="hint">
                  Inference hook: <code>GET /v1/models/{modelId}/versions/{version}/generation-profile</code>.
                  The hooks/ directory has HF generate and vLLM examples.
                </p>
              </>
            )}
          </>
        )}
      </div>
    </>
  );
}
