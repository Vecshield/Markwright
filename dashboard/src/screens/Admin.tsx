import { useCallback, useEffect, useState } from "react";

import { api, ApiError, AuditEntry, Policy, ResultRow } from "../api";

/** Policy (admin-editable, audited), recent verification activity (role-scoped),
 * and the tail of the tamper-evident audit log. */
export default function Admin({ role }: { role: string }) {
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [fpr, setFpr] = useState("");
  const [retention, setRetention] = useState("");
  const [results, setResults] = useState<ResultRow[]>([]);
  const [audit, setAudit] = useState<{ head: string; entries: AuditEntry[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [auditError, setAuditError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const refresh = useCallback(() => {
    api.policy().then((p) => {
      setPolicy(p);
      setFpr(p.fpr_target.toExponential(0));
      setRetention(String(p.retention_days));
    }).catch((e) => setError(String(e)));
    api.results().then(setResults).catch(() => setResults([]));
    api.audit().then(setAudit).catch((e) =>
      setAuditError(e instanceof ApiError && e.status === 403
        ? "Audit is readable by the auditor and admin roles only."
        : String(e)));
  }, []);
  useEffect(refresh, [refresh]);

  return (
    <>
      <div className="card">
        <h2>Policy</h2>
        <p className="hint">
          The false-positive target sets how often the deployment accepts a chance match:
          evidence bands are derived from it, and a verifier can lower it for one request but
          never raise it. Changes are audited.
        </p>
        {error && <div className="error">{error}</div>}
        <div className="row">
          <label>false-positive target{" "}
            <select className="field" value={fpr} onChange={(e) => setFpr(e.target.value)}>
              {["1e-2", "1e-3", "1e-4", "1e-5"].map((v) => <option key={v}>{v}</option>)}
            </select>
          </label>
          <label>result retention (days){" "}
            <input type="number" min={0} value={retention} style={{ width: "6rem" }}
              onChange={(e) => setRetention(e.target.value)} />
          </label>
          <button disabled={role !== "admin"} title={role !== "admin" ? "admin only" : ""}
            onClick={async () => {
              setError(null);
              setSaved(false);
              try {
                await api.putPolicy({ fpr_target: Number(fpr), retention_days: Number(retention) });
                setSaved(true);
                refresh();
              } catch (e) {
                setError(e instanceof ApiError ? e.message : String(e));
              }
            }}>Save (audited)</button>
          {saved && <span className="okline">✓ saved</span>}
        </div>
        {policy && (
          <p className="hint" style={{ marginTop: "0.6rem" }}>
            A tighter target needs a calibration that can resolve it. The bundled starter corpora
            resolve p-values down to about 3e-3, so a 1e-4 target caps every band at <i>weak</i>
            until you recalibrate on a production-size corpus; the result card states the cap and
            the reason.
          </p>
        )}
      </div>

      <div className="card">
        <h2>Verification activity</h2>
        <p className="hint">Metadata only; submitted text is never stored, so none can be shown.
          {role === "verifier" || role === "operator" ? " You see your own requests; auditors see the deployment's." : ""}
        </p>
        <table>
          <thead>
            <tr><th>when</th><th>by</th><th>model</th><th>tokens</th><th>band</th><th>fpr</th></tr>
          </thead>
          <tbody>
            {results.slice(0, 15).map((r) => (
              <tr key={r.result_id}>
                <td>{r.requested_at.slice(0, 19)}</td>
                <td>{r.requested_by ?? "—"}</td>
                <td>{r.model_id ? `${r.model_id} ${r.model_version}` : "—"}</td>
                <td>{r.token_count}</td>
                <td><span className={`band band-${r.band}`} style={{ padding: "0.1rem 0.5rem", fontSize: "0.78rem" }}>
                  {r.band.replace(/_/g, " ")}</span></td>
                <td>{r.fpr_target.toExponential(0)}</td>
              </tr>
            ))}
            {results.length === 0 && <tr><td colSpan={6} className="hint">no results yet</td></tr>}
          </tbody>
        </table>
      </div>

      <div className="card">
        <h2>Audit log</h2>
        {auditError && <div className="error">{auditError}</div>}
        {audit && (
          <>
            <p className="hint">
              Hash-chained and Ed25519-anchored; every mutation and every denial lands here. Export
              an offline-verifiable evidence bundle with <code>markwright audit export</code> /{" "}
              <code>POST /v1/audit/export</code>. Current head:{" "}
              <code>{audit.head.slice(0, 16)}…</code>
            </p>
            <table>
              <thead><tr><th>#</th><th>when</th><th>actor</th><th>action</th><th>target</th><th>outcome</th></tr></thead>
              <tbody>
                {audit.entries.slice(-12).reverse().map((e) => (
                  <tr key={e.seq}>
                    <td>{e.seq}</td>
                    <td>{e.ts.slice(0, 19)}</td>
                    <td>{e.actor.principal} ({e.actor.role})</td>
                    <td>{e.action}</td>
                    <td>{e.target.kind}:{e.target.id.slice(0, 10)}…</td>
                    <td>{e.outcome}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
    </>
  );
}
