import { useEffect, useState } from "react";

import { api, fmtFpr, getTheme, getToken, roleOf, setTheme, setToken } from "./api";
import Admin from "./screens/Admin";
import Keys from "./screens/Keys";
import Onboard from "./screens/Onboard";
import Overview from "./screens/Overview";
import Registry from "./screens/Registry";
import Schemes from "./screens/Schemes";
import Verify from "./screens/Verify";

const ROLES = ["verifier-token", "operator-token", "auditor-token", "admin-token"];
const SCREENS = ["overview", "verify", "schemes", "registry", "onboard", "keys", "admin"] as const;
type Screen = (typeof SCREENS)[number];

/** Which roles get full use of a screen. Others still see it, with a calm
 * notice instead of an error, so the role model is legible by exploring. */
const SCREEN_ROLES: Record<Screen, string[]> = {
  overview: ["verifier", "operator", "auditor", "admin"],
  verify: ["verifier", "operator", "auditor", "admin"],
  schemes: ["verifier", "operator", "auditor", "admin"],
  registry: ["verifier", "operator", "auditor", "admin"],
  onboard: ["operator", "admin"],
  keys: ["operator", "auditor", "admin"],
  admin: ["auditor", "admin"],
};

function screenFromHash(): Screen {
  const h = window.location.hash.replace("#", "") as Screen;
  return SCREENS.includes(h) ? h : "overview";
}

export default function App() {
  const [screen, setScreen] = useState<Screen>(screenFromHash);
  const [token, setTok] = useState(getToken());
  const [theme, setThm] = useState(getTheme());
  const [fpr, setFpr] = useState<number | null>(null);

  useEffect(() => setTheme(theme), [theme]);

  // Re-read the policy whenever the role changes: the header must never show a
  // value fetched as a different principal.
  useEffect(() => {
    let live = true;
    api.policy()
      .then((p) => live && setFpr(p.fpr_target))
      .catch(() => live && setFpr(null));
    return () => {
      live = false;
    };
  }, [token]);

  useEffect(() => {
    const onHash = () => setScreen(screenFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const role = roleOf(token);
  const go = (s: Screen) => (e: React.MouseEvent) => {
    e.preventDefault();
    window.location.hash = s;
    setScreen(s);
  };

  // Keying every screen by the token remounts it on a role change, so each one
  // refetches as the new principal instead of showing the previous role's data.
  const props = { role, key: token };

  return (
    <>
      <header>
        <a className="logo" href="#overview" onClick={go("overview")}>
          <img src="/brand/custody.svg" alt="" />
          <b>Markwright</b>
        </a>
        <nav>
          {SCREENS.map((s) => (
            <a
              key={s}
              href={`#${s}`}
              className={screen === s ? "active" : SCREEN_ROLES[s].includes(role) ? "" : "dim"}
              onClick={go(s)}
              title={SCREEN_ROLES[s].includes(role) ? "" : `limited for the ${role} role`}
            >
              {s[0].toUpperCase() + s.slice(1)}
            </a>
          ))}
        </nav>
        <div className="spacer" />
        {fpr !== null && (
          <span className="fprchip" title={`deployment false-positive target: ${fpr.toExponential(1)}`}>
            false-positive target {fmtFpr(fpr)}
          </span>
        )}
        <label className="rolepick">
          <span className="hint">role</span>
          <select
            value={token}
            onChange={(e) => {
              setToken(e.target.value);
              setTok(e.target.value);
            }}
            title="demo tokens; a real deployment assigns roles through OIDC"
          >
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {roleOf(r)}
              </option>
            ))}
          </select>
        </label>
        <button
          className="themebtn ghost"
          onClick={() => setThm(theme === "midnight" ? "frost" : "midnight")}
          title="theme"
        >
          {theme === "midnight" ? "☾ midnight" : "☀ frost"}
        </button>
      </header>
      <main>
        {screen === "overview" && <Overview {...props} onNavigate={(s) => {
          window.location.hash = s;
          setScreen(s as Screen);
        }} />}
        {screen === "verify" && <Verify {...props} />}
        {screen === "schemes" && <Schemes {...props} />}
        {screen === "registry" && <Registry {...props} />}
        {screen === "onboard" && <Onboard {...props} />}
        {screen === "keys" && <Keys {...props} />}
        {screen === "admin" && <Admin {...props} />}
      </main>
      <footer>
        A result measures the evidence for one registered watermark configuration. It cannot say
        whether a person or a machine wrote the text: unmarked models, paraphrase and translation
        all produce "no evidence", and at the false-positive target shown with every result, some
        positives are chance. Submitted text is never stored. The full responsible-use text is in
        docs/limitations.md.
      </footer>
    </>
  );
}
