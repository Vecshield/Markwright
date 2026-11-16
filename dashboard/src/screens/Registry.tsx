import { useCallback, useEffect, useState } from "react";

import { api, ApiError, CalibrationRun, Config, ModelRow } from "../api";

/** Models × versions × configs, with the operator actions per config:
 * calibrate → accept → activate → deprecate → retire, detector publish. */
export default function Registry({ role }: { role: string }) {
  const [models, setModels] = useState<ModelRow[]>([]);
  const [configs, setConfigs] = useState<Config[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [runs, setRuns] = useState<Record<string, CalibrationRun[]>>({});
  const [corpora, setCorpora] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const canOperate = role === "operator" || role === "admin";

  const refresh = useCallback(() => {
    Promise.all([api.models(), api.configs()])
      .then(([m, c]) => { setModels(m); setConfigs(c); })
      .catch((e) => setError(String(e)));
  }, []);   // remounted per role by App's key={token}, so one fetch per principal

  useEffect(() => {
    refresh();
    api.corpora().then(setCorpora).catch(() => setCorpora([]));
  }, [refresh]);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  function toggle(id: string) {
    setOpen(open === id ? null : id);
    if (open !== id) {
      api.calibrations(id).then((r) => setRuns((old) => ({ ...old, [id]: r }))).catch(() => undefined);
    }
  }

  return (
    <>
      {error && <div className="error">{error}</div>}
      {models.length === 0 && (
        <div className="card hint">
          No models registered yet. Use the Onboard wizard, the CLI
          (<code>markwright model add …</code>) or the API.
        </div>
      )}
      {models.map((m) => {
        const rows = configs.filter((c) => c.model_id === m.model_id);
        return (
          <div className="card" key={m.model_id}>
            <h2>{m.model_id}{m.display_name ? ` — ${m.display_name}` : ""}</h2>
            <table>
              <thead>
                <tr>
                  <th>version</th><th>config</th><th>v</th><th>scheme</th><th>status</th>
                  <th>calibration</th><th>min detectable</th><th>detector</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((c) => (
                  <RowWithDrawer key={c.config_id} c={c} open={open === c.config_id}
                    onToggle={() => toggle(c.config_id)} runs={runs[c.config_id] ?? []}
                    corpora={corpora} canOperate={canOperate} busy={busy} act={act} />
                ))}
                {rows.length === 0 && (
                  <tr><td colSpan={8} className="hint">no configs</td></tr>
                )}
              </tbody>
            </table>
          </div>
        );
      })}
    </>
  );
}

function RowWithDrawer({ c, open, onToggle, runs, corpora, canOperate, busy, act }: {
  c: Config; open: boolean; onToggle: () => void; runs: CalibrationRun[];
  corpora: string[]; canOperate: boolean; busy: boolean;
  act: (fn: () => Promise<unknown>) => Promise<void>;
}) {
  const [corpus, setCorpus] = useState(corpora[0] ?? "");
  useEffect(() => { if (!corpus && corpora.length) setCorpus(corpora[0]); }, [corpora, corpus]);
  const latestRun = runs[runs.length - 1];
  const calibrated = c.calibration_ref !== undefined && c.calibration_ref !== null;

  return (
    <>
      <tr className="clickable" onClick={onToggle}>
        <td>{c.model_version}</td>
        <td>{c.config_id.slice(0, 8)}…</td>
        <td>{c.config_version}</td>
        <td>{c.scheme_id}</td>
        <td><span className={`status-chip ${c.status}`}>{c.status}</span></td>
        <td>{c.calibration_ref === undefined ? "—" : c.calibration_ref ? "accepted" : "none"}</td>
        <td>{c.verification_defaults?.min_detectable_tokens ?? "–"}</td>
        <td>{c.detector_ref ? "published (can be forged)" : "private key"}</td>
      </tr>
      {open && (
        <tr className="drawer">
          <td colSpan={8}>
            {c.scheme_params && (
              <dl className="facts">
                <dt>params</dt><dd>{JSON.stringify(c.scheme_params)}</dd>
                <dt>tokenizer</dt><dd>{c.tokenizer_ref} {c.verification_defaults?.tokenizer_hash
                  ? `(hash ${String(c.verification_defaults.tokenizer_hash).slice(0, 10)}…)` : "(hash unrecorded)"}</dd>
                <dt>key</dt><dd>{c.key_ref?.slice(0, 13)}… (reference; the secret stays in the backend)</dd>
                <dt>runs</dt><dd>{runs.length ? runs.map((r) =>
                  `${r.run_id.slice(0, 8)}…(${r.status})`).join(", ") : "none"}</dd>
              </dl>
            )}
            {!c.scheme_params && <p className="hint">Scheme parameters and key references are not shown to the verifier role. Switch role in the header to see them.</p>}
            {canOperate && (
              <div className="row">
                {c.status === "draft" && !calibrated && (
                  <>
                    <select className="field" value={corpus} onChange={(e) => setCorpus(e.target.value)}>
                      {corpora.map((x) => <option key={x}>{x}</option>)}
                    </select>
                    <button disabled={busy || !corpus}
                      onClick={() => act(() => api.calibrate(c.config_id, [corpus]))}>
                      Run calibration
                    </button>
                  </>
                )}
                {latestRun && latestRun.status !== "accepted" && (
                  <button disabled={busy}
                    onClick={() => act(() => api.acceptCalibration(latestRun.run_id))}>
                    Accept run {latestRun.run_id.slice(0, 8)}…
                  </button>
                )}
                {c.status === "draft" && calibrated && (
                  <button disabled={busy} onClick={() => act(() => api.transition(c.config_id, "activate"))}>
                    Activate
                  </button>
                )}
                {c.status === "active" && (
                  <button className="ghost" disabled={busy}
                    onClick={() => act(() => api.transition(c.config_id, "deprecate"))}>
                    Deprecate (stops marking only)
                  </button>
                )}
                {c.status === "deprecated" && (
                  <button className="ghost" disabled={busy}
                    onClick={() => act(() => api.transition(c.config_id, "retire"))}>
                    Retire
                  </button>
                )}
                {!c.detector_ref && c.status === "active" && (
                  <button className="ghost" disabled={busy} title="Discloses key material for this
scheme; every later result under this config carries spoofing_not_excluded."
                    onClick={() => {
                      if (window.confirm("Publishing a detector DISCLOSES key material for this scheme. "
                        + "Every later result under this config will carry spoofing_not_excluded, forever. Publish?")) {
                        void act(() => api.publishDetector(c.config_id));
                      }
                    }}>
                    Publish detector…
                  </button>
                )}
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}
