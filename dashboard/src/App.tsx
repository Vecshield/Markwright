import { useEffect, useState } from "react";

import { api, getToken, setToken } from "./api";
import Registry from "./screens/Registry";
import Verify from "./screens/Verify";

const ROLES = ["verifier-token", "operator-token", "auditor-token", "admin-token"];

export default function App() {
  const [screen, setScreen] = useState<"verify" | "registry">("verify");
  const [token, setTok] = useState(getToken());
  const [fpr, setFpr] = useState<number | null>(null);

  useEffect(() => {
    api.policy().then((p) => setFpr(p.fpr_target)).catch(() => setFpr(null));
  }, [token]);

  return (
    <>
      <header>
        <img src="/brand/wordmark-horizontal-light.svg" alt="Markwright" className="wordmark" />
        <nav>
          <a
            href="#verify"
            className={screen === "verify" ? "active" : ""}
            onClick={() => setScreen("verify")}
          >
            Verify
          </a>
          <a
            href="#registry"
            className={screen === "registry" ? "active" : ""}
            onClick={() => setScreen("registry")}
          >
            Registry
          </a>
        </nav>
        <div className="spacer" />
        {fpr !== null && <span className="hint">fpr target {fpr.toExponential(0)}</span>}
        <select
          value={token}
          onChange={(e) => {
            setToken(e.target.value);
            setTok(e.target.value);
          }}
          title="dev tokens; v1 uses OIDC"
        >
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {r.replace("-token", "")}
            </option>
          ))}
        </select>
      </header>
      <main>{screen === "verify" ? <Verify /> : <Registry />}</main>
      <footer>
        A result is statistical evidence about one registered watermark configuration — never a
        statement that a person or a machine wrote the text. “No evidence” is not “human-written”:
        unmarked models, paraphrase and translation all produce it. Strong evidence is not proof; at
        the false-positive target shown with every result, some positives are chance. Submitted text
        is never stored. See docs/limitations.md for the full responsible-use text.
      </footer>
    </>
  );
}
