import { useState } from "react";

import { api, SchemeCatalogueEntry } from "../api";
import { Panel, useAsync } from "../components";

/** How a scheme becomes a live configuration. Drawn once here so an operator
 * can see the whole path before starting the wizard. */
function OnboardingDiagram() {
  const steps = [
    ["register", "model + version\n+ tokenizer"],
    ["configure", "scheme params\nvalidated by its schema"],
    ["key", "created in the backend;\nregistry keeps a reference"],
    ["calibrate", "score a human corpus,\nfit the null model"],
    ["accept", "operator signs off;\ntokenizer fingerprint saved"],
    ["activate", "marking starts;\nverification begins"],
  ];
  const w = 1080;
  const boxW = 150;
  const gap = (w - steps.length * boxW) / (steps.length - 1);
  return (
    <svg viewBox={`0 0 ${w} 190`} className="diagram" role="img"
      aria-label="Onboarding path: register, configure, key, calibrate, accept, activate">
      {steps.map(([title, body], i) => {
        const x = i * (boxW + gap);
        return (
          <g key={title}>
            <rect x={x} y={34} width={boxW} height={92} rx={10}
              className={i === steps.length - 1 ? "dg-box dg-final" : "dg-box"} />
            <text x={x + boxW / 2} y={58} className="dg-title">{`${i + 1}. ${title}`}</text>
            {body.split("\n").map((line, j) => (
              <text key={line} x={x + boxW / 2} y={80 + j * 16} className="dg-body">{line}</text>
            ))}
            {i < steps.length - 1 && (
              <path d={`M ${x + boxW + 6} 80 L ${x + boxW + gap - 6} 80`} className="dg-arrow"
                markerEnd="url(#dg-head)" />
            )}
          </g>
        );
      })}
      <defs>
        <marker id="dg-head" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
          <path d="M0,0 L8,4 L0,8 z" className="dg-arrowhead" />
        </marker>
      </defs>
      <text x={0} y={162} className="dg-note">
        A configuration cannot be activated until a calibration is accepted: without a null model
        there is no p-value, only a raw score.
      </text>
      <text x={0} y={180} className="dg-note">
        Rotation later creates a new configuration version on a new key. The old one stops marking
        and keeps verifying.
      </text>
    </svg>
  );
}

function SchemeCard({ s }: { s: SchemeCatalogueEntry }) {
  const [open, setOpen] = useState(false);
  const e = s;
  return (
    <div className="card">
      <div className="row" style={{ marginTop: 0, justifyContent: "space-between" }}>
        <div>
          <h2 style={{ marginBottom: 2 }}>{e.title}</h2>
          <span className="hint">
            <code>{e.scheme_id}</code> · {e.citation} ·{" "}
            <span className={`status-chip ${e.installed ? "active" : ""}`}>
              {e.installed ? `installed v${e.scheme_version}` : e.status}
            </span>
          </span>
        </div>
        <button className="ghost" onClick={() => setOpen(!open)}>
          {open ? "less" : "parameters and details"}
        </button>
      </div>

      <dl className="facts" style={{ marginTop: "0.9rem" }}>
        <dt>how it marks</dt><dd className="prose">{e.how_it_marks}</dd>
        <dt>how it detects</dt><dd className="prose">{e.how_it_detects}</dd>
        <dt>statistic</dt><dd>{e.statistic}</dd>
        <dt>key material</dt><dd className="prose">{e.key_material}</dd>
      </dl>

      <div className="grid2" style={{ marginTop: "0.9rem" }}>
        <div>
          <h3 className="minih">Strengths</h3>
          <ul className="tight">{e.strengths.map((x) => <li key={x}>{x}</li>)}</ul>
        </div>
        <div>
          <h3 className="minih">Limits</h3>
          <ul className="tight">{e.limits.map((x) => <li key={x}>{x}</li>)}</ul>
        </div>
      </div>

      {open && (
        <>
          <h3 className="minih" style={{ marginTop: "0.9rem" }}>Parameters</h3>
          <table>
            <thead><tr><th>parameter</th><th>what it controls</th><th>default here</th></tr></thead>
            <tbody>
              {Object.entries(e.params).map(([k, v]) => (
                <tr key={k}>
                  <td>{k}</td>
                  <td style={{ fontFamily: "inherit" }}>{v}</td>
                  <td>{e.defaults && k in e.defaults ? JSON.stringify(e.defaults[k]) : "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {e.capabilities && (
            <p className="hint" style={{ marginTop: "0.6rem" }}>
              Capabilities the wizard reads:{" "}
              {e.capabilities.distortion_free ? "distortion-free" : "changes the sampling distribution"},{" "}
              {e.capabilities.publishable_detector
                ? "detector can be published (which discloses key material)"
                : "no publishable detector; third parties need a detect-only service"},{" "}
              detection cost {String(e.capabilities.detect_cost)}.
            </p>
          )}
          {!e.installed && (
            <p className="hint">
              To add it: build a package that registers the <code>markwright.schemes</code> entry
              point, then <code>pip install</code> it. The core needs no change;{" "}
              <code>packages/markwright-scheme-kgw</code> is the worked example.
            </p>
          )}
        </>
      )}
    </div>
  );
}

export default function Schemes() {
  const state = useAsync(() => api.catalogue(), []);
  return (
    <>
      <div className="card">
        <h2>How a scheme becomes a live configuration</h2>
        <OnboardingDiagram />
      </div>
      <Panel state={state} what="The scheme catalogue" roles={["verifier", "operator", "auditor", "admin"]}>
        {(list) => (
          <>
            <p className="hint" style={{ margin: "0 0 0.8rem" }}>
              Markwright operates published schemes and writes none of its own. Installed schemes
              can be configured now; the others describe what adding them involves.
            </p>
            {list.map((s) => <SchemeCard key={s.scheme_id} s={s} />)}
          </>
        )}
      </Panel>
    </>
  );
}
