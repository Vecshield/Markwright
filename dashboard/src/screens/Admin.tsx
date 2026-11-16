import { useState } from "react";

import { api, AuditEntry, fmtFpr, Policy, ResultRow } from "../api";
import { ErrorBanner, Panel, Toast, useAsync } from "../components";

/** Policy (admin-editable, audited), recent verification activity (role-scoped),
 * and the tail of the tamper-evident audit log. */
export default function Admin({ role }: { role: string }) {
  const policyState = useAsync(() => api.policy(), [role]);
  const resultsState = useAsync(() => api.results(), [role]);
  const auditState = useAsync(() => api.audit(), [role]);
  const [fpr, setFpr] = useState("");
  const [retention, setRetention] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState<string | null>(null);

  const policy: Policy | null = policyState.data;
  // seed the editable fields once the policy arrives for this role
  if (policy && fpr === "") {
    setFpr(policy.fpr_target.toExponential(0));
    setRetention(String(policy.retention_days));
  }

  return (
    <>
      {saved && <Toast text={saved} onDone={() => setSaved(null)} />}
      <div className="card">
        <h2>Policy</h2>
        <p className="hint">
          The false-positive target sets how often the deployment accepts a chance match:
          evidence bands are derived from it, and a verifier can lower it for one request but
          never raise it. Changes are audited.
        </p>
        {error != null && <ErrorBanner error={error} />}
        {role !== "admin" && (
          <p className="hint">Editing policy is limited to the admin role; the current values are
            shown so every role can see the bar their results are judged against.</p>
        )}
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
              try {
                const p = await api.putPolicy({ fpr_target: Number(fpr), retention_days: Number(retention) });
                setSaved(`policy saved: false-positive target ${fmtFpr(p.fpr_target)}, retention ${p.retention_days} days`);
                policyState.reload();
              } catch (e) {
                setError(e);
              }
            }}>Save (audited)</button>
        </div>
        {policy && (
          <p className="hint" style={{ marginTop: "0.6rem" }}>
            A tighter target needs a calibration that can resolve it. The bundled starter corpora
            resolve p-values down to about 3e-3, so a 1e-4 target caps every band at <i>weak</i>{" "}
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
        <Panel state={resultsState} what="Verification activity" roles={["verifier", "operator", "auditor", "admin"]}>
        {(results: ResultRow[]) => (
        <table>
          <thead>
            <tr><th>when</th><th>by</th><th>model</th><th>tokens</th><th>band</th><th>false-positive target</th></tr>
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
                <td>{fmtFpr(r.fpr_target)}</td>
              </tr>
            ))}
            {results.length === 0 && <tr><td colSpan={6} className="hint">no verifications yet</td></tr>}
          </tbody>
        </table>
        )}
        </Panel>
      </div>

      <div className="card">
        <h2>Audit log</h2>
        <Panel state={auditState} what="The audit log" roles={["auditor", "admin"]}>
        {(audit: { head: string; entries: AuditEntry[] }) => (
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
        </Panel>
      </div>
    </>
  );
}
