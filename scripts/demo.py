"""Milestone A exit gate (design §11 MVP gate), against a LIVE server.

Run via scripts/demo.sh (which starts the API), or point MARKWRIGHT_URL at a
running instance. Steps: register → key → marked generation (watermark-bias
sampling through the generation profile — no LM needed; swap in
`markwright generate test` + a real model for the full experience) →
provisional calibration → activate → verify marked / human / short → audit
chain verifies offline.

Exits non-zero the moment any step's claim does not hold.
"""
from __future__ import annotations

import os
import sys

import httpx

BASE = os.environ.get("MARKWRIGHT_URL", "http://localhost:8723")
OP = {"Authorization": "Bearer operator-token"}
VER = {"Authorization": "Bearer verifier-token"}
AUD = {"Authorization": "Bearer auditor-token"}


def step(msg: str) -> None:
    print(f"\n==> {msg}")


def main() -> None:
    c = httpx.Client(base_url=BASE, timeout=300)

    step("1/8 register model + version")
    assert c.post("/v1/models", json={"model_id": "demo-llm"}, headers=OP).status_code in (201, 409)
    c.post("/v1/models/demo-llm/versions",
           json={"model_version": "1.0", "tokenizer_ref": "gpt2"}, headers=OP)

    step("2/8 create config (new key lineage in the local sealed vault)")
    cfg = c.post("/v1/models/demo-llm/versions/1.0/configs",
                 json={"scheme_id": "synthid-text"}, headers=OP).json()
    config_id = cfg["config_id"]
    print(f"    config {config_id} status={cfg['status']}")

    step("3/8 calibrate on the bundled null corpus (provisional, and says so)")
    run = c.post(f"/v1/configs/{config_id}/calibrations",
                 json={"null_corpus_ids": ["gutenberg-en-v1"]}, headers=OP).json()
    print(f"    quality={run['quality']} floor={run['resolution_floor']:.1e} docs={run['n_docs']}")
    assert run["quality"] == "provisional"
    assert c.post(f"/v1/calibrations/{run['run_id']}:accept", headers=OP).status_code == 200

    step("4/8 activate (allowed in dev profile despite provisional)")
    r = c.post(f"/v1/configs/{config_id}:activate", headers=OP)
    assert r.status_code == 200 and r.json()["status"] == "active", r.text

    step("5/8 marked generation via the generation-profile (processor-driven)")
    import torch

    from markwright.schemes import get
    from markwright.schemes.base import KeyHandle
    prof = c.get("/v1/models/demo-llm/versions/1.0/generation-profile", headers=OP).json()
    key = KeyHandle(prof["key_ref"], prof["key_version"], prof["backend"], prof["backend_ref"],
                    bytes.fromhex(prof["sealed_material"]))
    adapter = get(prof["scheme_id"])
    proc = adapter.apply(adapter.generate_config(prof["scheme_params"], key, prof["tokenizer_ref"]))
    torch.manual_seed(11)
    ids = torch.randint(100, 50000, (1, 6))
    for _ in range(300):
        biased = proc(ids, torch.zeros(1, 50000))
        ids = torch.cat([ids, torch.multinomial(torch.softmax(biased / 0.7, -1), 1)], dim=1)
    marked = ids[0].tolist()
    print(f"    {len(marked)} marked tokens (no LM: uniform logits + watermark bias)")

    step("6/8 verify marked → a positive band, with caveats, never a yes/no")
    res = c.post("/v1/verify", json={"token_ids": marked}, headers=VER).json()
    best = next(x for x in res["candidates"] if x["config_id"] == res["best_match"])
    print(f"    band={res['band']}  p_adj={best['p_value_adjusted']:.1e}  "
          f"stat={best['statistic']['value']:.3f}")
    print(f"    caveats: {', '.join(res['caveats'])}")
    assert res["band"] in ("moderate_evidence", "strong_evidence")
    assert "provisional_calibration" in res["caveats"]

    step("7/8 verify human text and a 30-token snippet")
    from markwright.calibration import load_corpus
    human = next(iter(load_corpus("gutenberg-en-v1")))
    res_h = c.post("/v1/verify", json={"token_ids": human.ids}, headers=VER).json()
    print(f"    human passage: band={res_h['band']}")
    assert res_h["band"] in ("no_evidence", "weak_evidence")
    res_s = c.post("/v1/verify", json={"token_ids": marked[:30]}, headers=VER).json()
    print(f"    30 tokens:     band={res_s['band']} (no p-value: {not res_s['candidates']})")
    assert res_s["band"] == "insufficient_evidence" and not res_s["candidates"]

    step("8/8 audit chain verifies")
    from markwright.audit import AuditChain
    audit = c.get("/v1/audit", headers=AUD).json()
    ok, msg = AuditChain.verify(audit["entries"])
    print(f"    {len(audit['entries'])} entries, chain: {msg}")
    assert ok
    for action in ("config.create", "key.create", "calibration.run", "calibration.accept",
                   "config.activate", "key.handle_issue", "verify.single"):
        assert any(e["action"] == action for e in audit["entries"]), f"missing audit action {action}"

    # -- optional step 9: second scheme from a separate package (design §12) ------------
    try:
        import importlib.util
        if importlib.util.find_spec("markwright_scheme_kgw") is None:
            raise ImportError
        step("9/9 [bonus] second scheme (kgw) from its own package — no core change")
        cfg2 = c.post("/v1/models/demo-llm/versions/1.0/configs",
                      json={"scheme_id": "kgw", "scheme_params": {"vocab_size": 50257}},
                      headers=OP).json()
        run2 = c.post(f"/v1/configs/{cfg2['config_id']}/calibrations",
                      json={"null_corpus_ids": ["gutenberg-en-v1"]}, headers=OP).json()
        c.post(f"/v1/calibrations/{run2['run_id']}:accept", headers=OP)
        c.post(f"/v1/configs/{cfg2['config_id']}:activate", headers=OP)
        res_multi = c.post("/v1/verify", json={"token_ids": marked}, headers=VER).json()
        schemes_scanned = {x["scheme_id"] for x in res_multi["candidates"]}
        best2 = next(x for x in res_multi["candidates"] if x["config_id"] == res_multi["best_match"])
        print(f"    scanned schemes: {sorted(schemes_scanned)}  (Bonferroni over "
              f"{len(res_multi['candidates'])} candidates)")
        print(f"    synthid-marked text matches: {best2['scheme_id']}  band={res_multi['band']}")
        assert schemes_scanned == {"synthid-text", "kgw"}
        assert best2["scheme_id"] == "synthid-text"
        assert "multiple_candidates" in res_multi["caveats"]
    except ImportError:
        print("\n(step 9 skipped: markwright-scheme-kgw not installed)")

    print("\nEXIT GATE PASSED: register → key → mark → calibrate → activate → verify, honestly labelled.")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"\nEXIT GATE FAILED: {e}", file=sys.stderr)
        sys.exit(1)
