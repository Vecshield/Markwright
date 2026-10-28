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

app = typer.Typer(help="Markwright: manage LLM text watermarks. Results are probabilistic evidence, never a verdict.")
model_app = typer.Typer(help="Register models and versions")
config_app = typer.Typer(help="Scheme configurations")
app.add_typer(model_app, name="model")
app.add_typer(config_app, name="config")
console = Console()

CAVEAT_TEXT = {
    "result_is_probabilistic": "result is probabilistic, not proof",
    "short_text": "text is short; evidence is limited",
    "low_entropy": "little free choice in this text (code, lists, quotes); marks are weak here",
    "possible_paraphrase": "paraphrase or editing can remove marks; a positive may be partial",
    "multiple_candidates": "several configurations were scanned; p-value was corrected for that",
    "historical_key": "matched a rotated or revoked key",
    "spoofing_not_excluded": "this configuration's detector is public; the mark could have been forged",
    "tokenizer_uncertain": "tokenizer mismatch; treat as unreliable",
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


@app.command()
def verify(text: str = typer.Option(None, "--text"), file: Path = typer.Option(None, "--file"),
           config: list[str] = typer.Option([], "--config"), fpr: float = typer.Option(None, "--fpr"),
           as_json: bool = typer.Option(False, "--json")):
    """Verify a text against registered configurations. Prints evidence, not a verdict."""
    if file:
        text = file.read_text()
    if not text:
        text = sys.stdin.read()
    body = {"text": text, "config_ids": config or None, "fpr_target": fpr}
    r = _client().post("/v1/verify", json=body)
    if r.status_code != 200:
        raise typer.Exit(_fail(r))
    res = r.json()
    if as_json:
        print(json.dumps(res, indent=2)); return
    _print_result(res)


def _print_result(res: dict) -> None:
    band = res["band"].replace("_", " ").upper()
    best = next((c for c in res["candidates"] if c["config_id"] == res["best_match"]), None)
    lines = [f"[bold]Result: {band}[/bold]"]
    if best:
        ci = best.get("ci_95")
        ci_s = f"  (95% interval {ci[0]:.1e} - {ci[1]:.1e})" if ci else ""
        lines += [f"  calibrated p-value    {best['p_value_adjusted']:.1e}{ci_s}, adjusted for {len(res['candidates'])} config(s) scanned",
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
    model = typer.prompt("model id (slug)"); version = typer.prompt("model version"); tok = typer.prompt("tokenizer (HF id or path)")
    model_add(model, version, tok)
    console.print("[bold]Step 2/5[/bold] pick a scheme"); schemes()
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
