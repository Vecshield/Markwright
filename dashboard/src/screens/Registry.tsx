import { useEffect, useState } from "react";

import { api, Config, ModelRow } from "../api";

export default function Registry() {
  const [models, setModels] = useState<ModelRow[]>([]);
  const [configs, setConfigs] = useState<Config[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.models(), api.configs()])
      .then(([m, c]) => {
        setModels(m);
        setConfigs(c);
      })
      .catch((e) => setError(String(e)));
  }, []);

  if (error) {
    return (
      <div className="card">
        <div className="error">{error}</div>
      </div>
    );
  }

  return (
    <>
      {models.length === 0 && (
        <div className="card hint">
          No models registered yet. Register one with{" "}
          <code>markwright model add &lt;id&gt; --version &lt;v&gt; --tokenizer &lt;ref&gt;</code> or the
          API.
        </div>
      )}
      {models.map((m) => {
        const rows = configs.filter((c) => c.model_id === m.model_id);
        return (
          <div className="card" key={m.model_id}>
            <h2 style={{ marginTop: 0 }}>
              {m.model_id}
              {m.display_name ? ` — ${m.display_name}` : ""}
            </h2>
            <table>
              <thead>
                <tr>
                  <th>version</th>
                  <th>config</th>
                  <th>v</th>
                  <th>scheme</th>
                  <th>status</th>
                  <th>calibration</th>
                  <th>min detectable</th>
                  <th>detector</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((c) => (
                  <tr key={c.config_id}>
                    <td>{c.model_version}</td>
                    <td>{c.config_id.slice(0, 8)}…</td>
                    <td>{c.config_version}</td>
                    <td>{c.scheme_id}</td>
                    <td>
                      <span className={`status-chip ${c.status}`}>{c.status}</span>
                    </td>
                    <td>
                      {c.calibration_ref === undefined
                        ? "—" /* verifier view: field withheld */
                        : c.calibration_ref
                          ? "accepted"
                          : "none (cannot activate)"}
                    </td>
                    <td>{c.verification_defaults?.min_detectable_tokens ?? "–"}</td>
                    <td>{c.detector_ref ? "published (spoofable!)" : "private key"}</td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr>
                    <td colSpan={8} className="hint">
                      no configs
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        );
      })}
    </>
  );
}
