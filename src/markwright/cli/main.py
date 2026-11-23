"""`markwright` CLI: a thin client over the REST API, plus offline verification
that imports the core library directly."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.panel import Panel

app = typer.Typer(help="Markwright: manage LLM text watermarks. Results are calibrated statistical evidence with an explicit false-positive target.")
model_app = typer.Typer(help="Register models and versions")
config_app = typer.Typer(help="Scheme configurations")
calibrate_app = typer.Typer(help="Fit and accept null models (required before activation)")
generate_app = typer.Typer(help="Test generation through the inference hook")
key_app = typer.Typer(help="Key lifecycle: metadata only; secrets stay in the backend")
audit_app = typer.Typer(help="Tamper-evident audit log: tail, export, verify offline")
detector_app = typer.Typer(help="Published detector artifacts (discloses key material!)")
bundle_app = typer.Typer(help="Offline verification bundles")
app.add_typer(model_app, name="model")
app.add_typer(config_app, name="config")
app.add_typer(calibrate_app, name="calibrate")
app.add_typer(generate_app, name="generate")
app.add_typer(key_app, name="key")
app.add_typer(audit_app, name="audit")
app.add_typer(detector_app, name="detector")
app.add_typer(bundle_app, name="bundle")
console = Console()

CAVEAT_TEXT = {
    "result_is_probabilistic": "statistical evidence; some human texts score this high by chance, at the rate shown",
    "short_text": "the text is short, so the evidence is limited",
    "low_entropy": "this text leaves the model little free choice (code, lists, quotes), so marks are weak in it",
    "possible_paraphrase": "paraphrase or editing can remove marks; a positive may cover only part of the text",
    "multiple_candidates": "several configurations were scanned and the p-value was corrected for that",
    "historical_key": "matched a rotated or revoked key",
    "spoofing_not_excluded": "this configuration's detector is public, so the mark could have been forged",
    "tokenizer_uncertain": "tokenizer unavailable or mismatched; this configuration was not scored",
    "provisional_calibration": "calibrated on a small starter corpus; evidence bands are capped at its resolution",
}


def _client() -> httpx.Client:
    base = os.environ.get("MARKWRIGHT_URL", "http://localhost:8000")
    token = os.environ.get("MARKWRIGHT_TOKEN", "operator-token")
    return httpx.Client(base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=120)


@app.command()
def schemes():
    """List installed scheme adapters and their capabilities."""
    for s in _client().get("/v1/schemes").json():
        caps = ", ".join(k for k, v in s["capabilities"].items() if v is True)
        console.print(f"[bold]{s['scheme_id']}[/bold] v{s['scheme_version']}  {caps}")


@model_app.command("add")
def model_add(model_id: str, version: str = typer.Option(..., "--version"), tokenizer: str = typer.Option(..., "--tokenizer")):
    c = _client()
    r = c.post("/v1/models", json={"model_id": model_id})
    if r.status_code not in (201, 409):
        raise typer.Exit(_fail(r))
    r = c.post(f"/v1/models/{model_id}/versions", json={"model_version": version, "tokenizer_ref": tokenizer})
    console.print(r.json() if r.status_code == 201 else _fail(r))


@config_app.command("create")
def config_create(model: str = typer.Option(...), version: str = typer.Option(...), scheme: str = typer.Option("synthid-text"),
                  param: list[str] = typer.Option([], help="k=v overrides"), key: str = "new"):
    params = {}
    for kv in param:
        k, _, v = kv.partition("=")
        params[k] = json.loads(v) if v and v[0] in "0123456789-[{tf" else v
    r = _client().post(f"/v1/models/{model}/versions/{version}/configs", json={"scheme_id": scheme, "scheme_params": params, "key": key})
    console.print(r.json() if r.status_code == 201 else _fail(r))


@config_app.command("activate")
def config_activate(config_id: str):
    r = _client().post(f"/v1/configs/{config_id}:activate")
    console.print(r.json() if r.status_code == 200 else _fail(r))


@calibrate_app.command("run")
def calibrate_run(config_id: str,
                  null_corpus: list[str] = typer.Option(["gutenberg-en-v1"], "--null-corpus"),
                  fpr: float = typer.Option(None, "--fpr")):
    """Fit a null model for CONFIG_ID on bundled corpora (synchronous)."""
    r = _client().post(f"/v1/configs/{config_id}/calibrations",
                       json={"null_corpus_ids": null_corpus, "fpr_target": fpr})
    if r.status_code != 201:
        raise typer.Exit(_fail(r))
    _print_calibration(r.json())
    console.print("Accept it with: [bold]markwright calibrate accept "
                  f"{r.json()['run_id']}[/bold]")


@calibrate_app.command("show")
def calibrate_show(run_id: str):
    r = _client().get(f"/v1/calibrations/{run_id}")
    _print_calibration(r.json()) if r.status_code == 200 else _fail(r)


@calibrate_app.command("accept")
def calibrate_accept(run_id: str):
    """Accept a run: its null model becomes the config's calibration."""
    r = _client().post(f"/v1/calibrations/{run_id}:accept")
    if r.status_code != 200:
        raise typer.Exit(_fail(r))
    run = r.json()
    _print_calibration(run)
    console.print(f"Config [bold]{run['config_id']}[/bold] can now be activated.")


@generate_app.command("test")
def generate_test(config: str = typer.Option(..., "--config"),
                  prompt: str = typer.Option(..., "--prompt"),
                  model: str = typer.Option(..., "--model", help="HF id or local path of the weights"),
                  max_new_tokens: int = typer.Option(300, "--max-new-tokens"),
                  seed: int = typer.Option(None, "--seed")):
    """Run one marked test generation through the inference hook (needs torch)."""
    cfg = _client().get(f"/v1/configs/{config}").json()
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        from markwright.schemes import get as get_scheme
        from markwright.schemes.base import KeyHandle
    except ImportError:
        console.print("[red]needs the synthid extra:[/red] uv pip install -e '.[synthid]'")
        raise typer.Exit(1) from None
    r = _client().get(f"/v1/models/{cfg['model_id']}/versions/{cfg['model_version']}/generation-profile",
                      params={"config_id": config})
    if r.status_code != 200:
        raise typer.Exit(_fail(r))
    prof = r.json()
    key = KeyHandle(prof["key_ref"], prof["key_version"], prof["backend"], prof["backend_ref"],
                    bytes.fromhex(prof["sealed_material"]))
    adapter = get_scheme(prof["scheme_id"])
    wm = adapter.hf_watermarking_config(prof["scheme_params"], key)
    if seed is not None:
        torch.manual_seed(seed)
    tok = AutoTokenizer.from_pretrained(model)
    lm = AutoModelForCausalLM.from_pretrained(model, torch_dtype="auto")
    inputs = tok(prompt, return_tensors="pt")
    s = prof["scheme_params"].get("sampling", {})
    out = lm.generate(**inputs, do_sample=True, temperature=s.get("temperature", 1.0),
                      top_k=s.get("top_k", 40), top_p=s.get("top_p", 0.99),
                      max_new_tokens=max_new_tokens, watermarking_config=wm)
    text = tok.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
    console.print(Panel(text, title=f"marked output ({prof['scheme_id']}, key v{prof['key_version']})",
                        subtitle="verify with: markwright verify --text '<this text>'"))


# ---- keys ----------------------------------------------------------------------------------
@key_app.command("create")
def key_create(owner: str = typer.Option(None, "--owner", help="model id this key belongs to")):
    r = _client().post("/v1/keys", json={"purpose": "generation", "owner_model_id": owner})
    console.print(r.json() if r.status_code == 201 else _fail(r))


@key_app.command("show")
def key_show(key_id: str):
    r = _client().get(f"/v1/keys/{key_id}")
    console.print(r.json() if r.status_code == 200 else _fail(r))


@key_app.command("list")
def key_list(lineage: str = typer.Option(None, "--lineage")):
    r = _client().get("/v1/keys", params={"lineage": lineage} if lineage else None)
    for k in r.json():
        console.print(f"[bold]{k['key_id']}[/bold] v{k['key_version']} {k['purpose']} "
                      f"state={k['state']} lineage={k['lineage_id'][:8]}…")


@key_app.command("rotate")
def key_rotate(key_id: str):
    """Old key verifies forever; configs move to a new version on the new key."""
    r = _client().post(f"/v1/keys/{key_id}:rotate")
    if r.status_code != 200:
        raise typer.Exit(_fail(r))
    out = r.json()
    console.print(f"rotated: new key [bold]{out['new_key']['key_id']}[/bold] "
                  f"v{out['new_key']['key_version']}")
    for c in out["configs"]:
        console.print(f"  config {c['old'][:8]}… deprecated -> new active config {c['new'][:8]}…")


@key_app.command("revoke")
def key_revoke(key_id: str, reason: str = typer.Option(..., "--reason")):
    """Compromise response: marking stops now; verification continues, flagged."""
    r = _client().post(f"/v1/keys/{key_id}:revoke", json={"reason": reason})
    console.print(r.json() if r.status_code == 200 else _fail(r))


# ---- audit ---------------------------------------------------------------------------------
@audit_app.command("tail")
def audit_tail(n: int = typer.Option(20, "-n")):
    r = _client().get("/v1/audit")
    if r.status_code != 200:
        raise typer.Exit(_fail(r))
    body = r.json()
    for e in body["entries"][-n:]:
        console.print(f"{e['seq']:>5} {e['ts'][:19]} {e['actor']['principal']:<8} "
                      f"{e['action']:<20} {e['target']['kind']}:{e['target']['id'][:13]} {e['outcome']}")
    console.print(f"head: {body['head']}")


@audit_app.command("export")
def audit_export(out: Path = typer.Option(Path("markwright-audit.tgz"), "--out")):
    """Signed evidence bundle, verifiable offline with the deployment public key."""
    r = _client().post("/v1/audit/export")
    if r.status_code != 200:
        raise typer.Exit(_fail(r))
    out.write_bytes(r.content)
    console.print(f"wrote {out} ({len(r.content)} bytes); verify with: "
                  f"[bold]markwright audit verify {out}[/bold]")


@audit_app.command("verify")
def audit_verify(bundle: Path, pubkey: Path = typer.Option(None, "--pubkey",
                 help="out-of-band hex public key file; defaults to the bundled one")):
    """Offline: no server, no account — signature, head and hash chain."""
    from markwright.audit import verify_bundle
    ok, msg = verify_bundle(bundle.read_bytes(),
                            pubkey.read_text(encoding="utf-8").strip() if pubkey else None)
    console.print(("[green]VALID[/green] " if ok else "[red]INVALID[/red] ") + msg)
    raise typer.Exit(0 if ok else 1)


# ---- OWM declaration -----------------------------------------------------------------------
declaration_app = typer.Typer(help="OWM repo declarations (watermark.owm.json)")
app.add_typer(declaration_app, name="declaration")


@declaration_app.command("export")
def declaration_export(config_id: str,
                       out: Path = typer.Option(Path("watermark.owm.json"), "--out")):
    """Export the public-safe, signed declaration for a configuration. Place the
    file in the model's repository so third parties can discover and verify the
    marking. Key references never appear; parameters appear only under a public
    disclosure."""
    r = _client().get(f"/v1/configs/{config_id}/declaration")
    if r.status_code != 200:
        raise typer.Exit(_fail(r))
    doc = r.json()
    out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    m = doc["marking"][0]
    console.print(f"wrote {out}")
    console.print(f"  subject      {doc['subject']['uri']}")
    console.print(f"  scheme       {m['scheme_id']} v{m['scheme_version']}")
    console.print(f"  fingerprint  {m['config_fingerprint'][:23]}…")
    console.print(f"  params       {'public' if m['params'] else 'fingerprint only'}")
    console.print(f"  signed       {'yes (deployment identity)' if doc['signature'] else 'no'}")


# ---- detectors / bundles -------------------------------------------------------------------
@detector_app.command("publish")
def detector_publish(config_id: str, out: Path = typer.Option(None, "--out")):
    """Publish the detector artifact. For SynthID this DISCLOSES the key list;
    results under this config will carry spoofing_not_excluded forever."""
    r = _client().post(f"/v1/configs/{config_id}/detector:publish")
    if r.status_code != 200:
        raise typer.Exit(_fail(r))
    body = r.json()
    console.print(f"published {body['detector_ref']} (sha256 {body['sha256'][:16]}…)")
    console.print(f"[yellow]{body['note']}[/yellow]")
    if out:
        d = _client().get(f"/v1/detectors/{config_id}")
        out.write_text(json.dumps(d.json(), indent=1), encoding="utf-8")
        console.print(f"wrote {out}")


@bundle_app.command("export")
def bundle_export(model: str = typer.Option(None, "--model"),
                  out: Path = typer.Option(Path("markwright-verify.bundle"), "--out")):
    """Export configs + null models + published detectors for offline verification."""
    r = _client().get("/v1/bundle", params={"model": model} if model else None)
    if r.status_code != 200:
        raise typer.Exit(_fail(r))
    out.write_bytes(r.content)
    console.print(f"wrote {out} ({len(r.content)/1e6:.1f} MB); use with: "
                  f"[bold]markwright verify --offline --bundle {out} --file <doc>[/bold]")


def _print_calibration(run: dict) -> None:
    lines = [f"[bold]calibration {run['run_id']}[/bold]  status={run['status']}  "
             f"quality={run['quality']}",
             f"  corpora    {', '.join(run['null_corpus_ids'])}  ({run['n_docs']} docs)",
             f"  resolution floor  {run['resolution_floor']:.1e}  "
             f"(fpr target {run['fpr_target']:.1e})",
             "  buckets    " + ", ".join(f"{b['length']}tok n={b['n']}" for b in run["buckets"])]
    lines += [f"  note: {n}" for n in run["notes"]]
    console.print(Panel("\n".join(lines), title="markwright calibrate"))


@app.command()
def verify(text: str = typer.Option(None, "--text"), file: Path = typer.Option(None, "--file"),
           config: list[str] = typer.Option([], "--config"), fpr: float = typer.Option(None, "--fpr"),
           offline: bool = typer.Option(False, "--offline", help="verify against a bundle, no server"),
           bundle: Path = typer.Option(None, "--bundle", help="bundle from `markwright bundle export`"),
           as_json: bool = typer.Option(False, "--json")):
    """Verify a text against registered configurations and print the evidence."""
    if file:
        text = file.read_text(encoding="utf-8")
    if not text:
        text = sys.stdin.read()
    if offline:
        if bundle is None:
            console.print("[red]--offline needs --bundle <file>[/red] (export one with `markwright bundle export`)")
            raise typer.Exit(1)
        from markwright.verify.offline import verify_offline
        result, loaded = verify_offline(bundle, text, fpr)
        res = result.payload
        for s in loaded.skipped:
            console.print(f"[yellow]skipped[/yellow] {s['config_id']}: {s['reason']}")
    else:
        body = {"text": text, "config_ids": config or None, "fpr_target": fpr}
        r = _client().post("/v1/verify", json=body)
        if r.status_code != 200:
            raise typer.Exit(_fail(r))
        res = r.json()
    if as_json:
        print(json.dumps(res, indent=2))
        return
    _print_result(res)


def _print_result(res: dict) -> None:
    band = res["band"].replace("_", " ").upper()
    best = next((c for c in res["candidates"] if c["config_id"] == res["best_match"]), None)
    lines = [f"[bold]Result: {band}[/bold]"]
    if best:
        ci = best.get("ci_95")
        ci_s = f"  (95% interval {ci[0]:.1e} to {ci[1]:.1e})" if ci else ""
        lines += [f"  calibrated p-value    {best['p_value_adjusted']:.1e}{ci_s}, corrected for {len(res['candidates'])} configuration(s) scanned",
                  f"  config                {best['config_id']}  ({best['scheme_id']}, key v{best['key_version']})"]
    lines += [f"  false-positive target {res['fpr_target']:.1e}  (deployment policy)",
              f"  tokens                {res['token_count']} total, {res['scored_token_count']} scored"
              + (f"  (minimum detectable for this config: {best['min_detectable_tokens']})" if best and best.get("min_detectable_tokens") else ""),
              "", f"  {res['band_reason']}", "", "  caveats:"]
    lines += [f"    - {CAVEAT_TEXT.get(c, c)}" for c in res["caveats"]]
    console.print(Panel("\n".join(lines), title="markwright verify", subtitle="absence of a mark is not evidence of human authorship"))


@app.command()
def init():
    """Five-step wizard: register model -> pick scheme -> keys -> test generation -> verify."""
    console.print("[bold]Step 1/5[/bold] register a model")
    model = typer.prompt("model id (slug)")
    version = typer.prompt("model version")
    tok = typer.prompt("tokenizer (HF id or path)")
    model_add(model, version, tok)
    console.print("[bold]Step 2/5[/bold] pick a scheme")
    schemes()
    scheme = typer.prompt("scheme", default="synthid-text")
    console.print("[bold]Step 3/5[/bold] keys: a new lineage is created in the configured backend (local sealed vault in dev)")
    config_create(model=model, version=version, scheme=scheme, param=[], key="new")
    console.print("[bold]Step 4/5[/bold] test generation: run `markwright generate test --config <id> --prompt ...` with transformers installed")
    console.print("[bold]Step 5/5[/bold] verify it: `markwright verify --text '<generated text>' --config <id>`")
    console.print("Before activating, run calibration on a null corpus: `markwright calibrate run <id> --null-corpus wiki-en`")


def _fail(r: httpx.Response) -> int:
    console.print(f"[red]{r.status_code}[/red] {r.text}")
    return 1


if __name__ == "__main__":
    app()
