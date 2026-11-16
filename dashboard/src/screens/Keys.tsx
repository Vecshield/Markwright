import { useCallback, useEffect, useState } from "react";

import { api, ApiError, KeyRow } from "../api";

/** Key lifecycle, metadata only: the registry stores references and the
 * backend holds the bytes. Rotation keeps old versions verifying; revocation
 * stops marking at once and flags every later match. */
export default function Keys({ role }: { role: string }) {
  const [keys, setKeys] = useState<KeyRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const refresh = useCallback(() => {
    api.keys().then(setKeys).catch((e) =>
      setError(e instanceof ApiError && e.status === 403
        ? "The verifier role cannot read key metadata. Switch to operator, auditor or admin."
        : String(e)));
  }, []);
  useEffect(refresh, [refresh]);

  async function act(fn: () => Promise<unknown>, note: string) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      setNotice(note);
      refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const lineages = [...new Set(keys.map((k) => k.lineage_id))];

  return (
    <>
      <div className="card">
        <h2>Keys</h2>
        <p className="hint">
          Each row is a reference to a key; the secret bytes stay in the key backend. A rotated
          key moves to <i>verify-only</i> and keeps verifying old text indefinitely. A revoked key
          stops marking at once, and every later match carries <code>historical_key</code> with
          the recorded reason. Generation keys and signing identities are separate records and the
          server refuses to mix them.
        </p>
        {error && <div className="error">{error}</div>}
        {notice && <p className="okline">✓ {notice}</p>}
        {lineages.map((lin) => (
          <table key={lin} style={{ marginBottom: "1rem" }}>
            <thead>
              <tr>
                <th>lineage {lin.slice(0, 8)}…</th><th>purpose</th><th>v</th><th>backend</th>
                <th>state</th><th>generates until</th><th>actions</th>
              </tr>
            </thead>
            <tbody>
              {keys.filter((k) => k.lineage_id === lin).map((k) => (
                <tr key={k.key_id}>
                  <td>{k.key_id.slice(0, 13)}…</td>
                  <td>{k.purpose}</td>
                  <td>{k.key_version}</td>
                  <td>{k.backend}</td>
                  <td>
                    <span className={`status-chip ${k.state}`}>{k.state}</span>
                    {k.revocation_reason && <span className="hint"> ({k.revocation_reason})</span>}
                  </td>
                  <td>{k.generate_until ? k.generate_until.slice(0, 19) : k.state === "active" ? "open" : "—"}</td>
                  <td>
                    {k.purpose === "generation" && k.state === "active" && (
                      <span className="row" style={{ margin: 0 }}>
                        <button className="ghost" disabled={busy || !(role === "operator" || role === "admin")}
                          onClick={() => act(() => api.rotateKey(k.key_id),
                            "rotated: the old key keeps verifying and configs moved to a new version")}>
                          Rotate
                        </button>
                        <button className="ghost" disabled={busy || role !== "admin"}
                          title="admin only"
                          onClick={() => {
                            const reason = window.prompt("Revocation reason (recorded in the audit log and on every later match):");
                            if (reason) void act(() => api.revokeKey(k.key_id, reason), "revoked: marking stopped, verification continues with the historical_key caveat");
                          }}>
                          Revoke…
                        </button>
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ))}
        {keys.length === 0 && !error && <p className="hint">No keys yet. Onboarding a model creates its first lineage.</p>}
      </div>
    </>
  );
}
