import { useEffect, useState } from "react";

import { api, getTheme, getToken, roleOf, setTheme, setToken } from "./api";
import Admin from "./screens/Admin";
import Keys from "./screens/Keys";
import Onboard from "./screens/Onboard";
import Overview from "./screens/Overview";
import Registry from "./screens/Registry";
import Verify from "./screens/Verify";

const ROLES = ["verifier-token", "operator-token", "auditor-token", "admin-token"];
const SCREENS = ["overview", "verify", "registry", "onboard", "keys", "admin"] as const;
type Screen = (typeof SCREENS)[number];

export default function App() {
  const [screen, setScreen] = useState<Screen>(
    () => ((window.location.hash.replace("#", "") as Screen) || "overview"));
  const [token, setTok] = useState(getToken());
  const [theme, setThm] = useState(getTheme());
  const [fpr, setFpr] = useState<number | null>(null);

  useEffect(() => {
    setTheme(theme);
  }, [theme]);

  useEffect(() => {
    api.policy().then((p) => setFpr(p.fpr_target)).catch(() => setFpr(null));
  }, [token]);

  const role = roleOf(token);
  const nav = (s: Screen) => () => {
    window.location.hash = s;
    setScreen(s);
  };

  return (
    <>
      <header>
        <span className="logo">
          <img src="/brand/custody.svg" alt="" />
          <b>Markwright</b>
        </span>
        <nav>
          {SCREENS.map((s) => (
            <a key={s} href={`#${s}`} className={screen === s ? "active" : ""} onClick={nav(s)}>
              {s[0].toUpperCase() + s.slice(1)}
            </a>
          ))}
        </nav>
        <div className="spacer" />
        {fpr !== null && <span className="fprchip">fpr {fpr.toExponential(0)}</span>}
        <select
          value={token}
          onChange={(e) => {
            setToken(e.target.value);
            setTok(e.target.value);
          }}
          title="dev tokens; production uses OIDC"
        >
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {roleOf(r)}
            </option>
          ))}
        </select>
        <button
          className="themebtn ghost"
          onClick={() => setThm(theme === "midnight" ? "frost" : "midnight")}
          title="theme (brand kit: Midnight / Frost)"
        >
          {theme === "midnight" ? "☾ midnight" : "☀ frost"}
        </button>
      </header>
      <main>
        {screen === "overview" && <Overview onNavigate={(s) => nav(s as Screen)()} />}
        {screen === "verify" && <Verify />}
        {screen === "registry" && <Registry role={role} />}
        {screen === "onboard" && <Onboard role={role} />}
        {screen === "keys" && <Keys role={role} />}
        {screen === "admin" && <Admin role={role} />}
      </main>
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
