"""Tier-2 tests for the KGW adapter package — AND the design-§12 architecture
claim: the core never changed; this scheme arrived purely via the
`markwright.schemes` entry point of markwright-scheme-kgw."""
from __future__ import annotations

import importlib.util

import pytest

if importlib.util.find_spec("torch") is None:
    pytest.skip("torch not installed (tier-2 suite)", allow_module_level=True)
if importlib.util.find_spec("markwright_scheme_kgw") is None:
    pytest.skip("markwright-scheme-kgw not installed", allow_module_level=True)

pytestmark = pytest.mark.torch

from markwright_scheme_kgw import KGWAdapter

from markwright import schemes

from .conftest import FIXTURE_KEY_V1, FIXTURE_KEY_V2, load_token_ids

A = KGWAdapter()
P = {**A.default_params(), "vocab_size": 50257}


def _marked_ids(n: int = 220, seed: int = 0, key=FIXTURE_KEY_V1) -> list[int]:
    """Watermark-bias-only generation, same device as the synthid fixtures."""
    import torch

    proc = A.apply(A.generate_config(P, key, "gpt2"))
    torch.manual_seed(seed)
    ids = torch.randint(100, P["vocab_size"] - 1, (1, 4))
    for _ in range(n):
        biased = proc(ids, torch.zeros(1, P["vocab_size"]))
        ids = torch.cat([ids, torch.multinomial(torch.softmax(biased, -1), 1)], dim=1)
    return ids[0].tolist()


def test_discovered_via_entry_point_without_core_changes():
    avail = schemes.available()
    assert "kgw" in avail and "synthid-text" in avail
    assert schemes.get("kgw").scheme_id == "kgw"
    # and the core's builtin table was NOT edited for this scheme
    assert "kgw" not in schemes._BUILTIN


def test_marked_detects_and_wrong_key_does_not():
    ids = _marked_ids()
    d = A.detect(ids, P, FIXTURE_KEY_V1, "gpt2")
    assert d.statistic_name == "kgw_z" and d.statistic_value > 8
    assert d.extra["green_fraction"] > 0.5 and d.scored_tokens > 150
    d2 = A.detect(ids, P, FIXTURE_KEY_V2, "gpt2")
    assert abs(d2.statistic_value) < 3            # wrong key: z is unremarkable
    assert abs(d2.extra["green_fraction"] - P["gamma"]) < 0.08


def test_unmarked_corpus_scores_near_gamma():
    for case in ("unmarked_200_0", "unmarked_400_0"):
        d = A.detect(load_token_ids(case), P, FIXTURE_KEY_V1, "gpt2")
        assert abs(d.extra["green_fraction"] - P["gamma"]) < 0.1
        assert abs(d.statistic_value) < 3.5


def test_no_booleans_escape_the_hf_detector():
    """transformers' detector returns `prediction`/`confidence`; the honesty
    rules forbid propagating them."""
    d = A.detect(_marked_ids(120), P, FIXTURE_KEY_V1, "gpt2")
    flat = {**d.extra, "stat": d.statistic_value, "scored": d.scored_tokens,
            "p": d.analytic_p_value}
    assert not any(isinstance(v, bool) for v in flat.values())
    assert "prediction" not in d.extra and "confidence" not in d.extra


def test_detector_key_is_not_publishable():
    assert A.export_detector(P, FIXTURE_KEY_V1) is None
    assert A.capabilities.publishable_detector is False
    from markwright.schemes.base import Detector
    with pytest.raises(ValueError, match="no publishable detector"):
        A.detect([1] * 100, P, Detector("artifact", "kgw", {}, "x"), "gpt2")


def test_hashing_key_is_derived_and_version_scoped():
    k1 = A._hashing_key(P, FIXTURE_KEY_V1)
    assert k1 == A._hashing_key(P, FIXTURE_KEY_V1) and k1 != A._hashing_key(P, FIXTURE_KEY_V2)
    assert 0 < k1 < 2**63


def test_full_api_loop_with_kgw(api):
    """Register → calibrate (real corpus, real adapter) → activate → verify,
    all through HTTP, alongside synthid — nothing in the core knows kgw exists."""
    from .conftest import OP, VERIFIER
    client, _ = api
    client.post("/v1/models", json={"model_id": "kgw-llm"}, headers=OP)
    client.post("/v1/models/kgw-llm/versions",
                json={"model_version": "1", "tokenizer_ref": "gpt2"}, headers=OP)
    cfg = client.post("/v1/models/kgw-llm/versions/1/configs",
                      json={"scheme_id": "kgw", "scheme_params": {"vocab_size": 50257}},
                      headers=OP)
    assert cfg.status_code == 201, cfg.text
    cid = cfg.json()["config_id"]
    run = client.post(f"/v1/configs/{cid}/calibrations",
                      json={"null_corpus_ids": ["gutenberg-en-v1"]}, headers=OP)
    assert run.status_code == 201, run.text
    assert run.json()["quality"] == "provisional"
    client.post(f"/v1/calibrations/{run.json()['run_id']}:accept", headers=OP)
    assert client.post(f"/v1/configs/{cid}:activate", headers=OP).status_code == 200

    # marked with THIS deployment's key, via the generation profile
    import torch

    from markwright.inference import build_hf_processor
    prof = client.get("/v1/models/kgw-llm/versions/1/generation-profile", headers=OP).json()
    proc = build_hf_processor(prof)
    torch.manual_seed(3)
    ids = torch.randint(100, 50256, (1, 4))
    for _ in range(250):
        biased = proc(ids, torch.zeros(1, 50257))
        ids = torch.cat([ids, torch.multinomial(torch.softmax(biased, -1), 1)], dim=1)

    res = client.post("/v1/verify", json={"token_ids": ids[0].tolist()}, headers=VERIFIER).json()
    assert res["band"] == "moderate_evidence"          # provisional floor; honest cap
    assert res["best_match"] == cid
    assert "provisional_calibration" in res["caveats"]
    # kgw's detector is NOT publishable → the route refuses with the capability reason
    r = client.post(f"/v1/configs/{cid}/detector:publish", headers=OP)
    assert r.status_code == 409 and "cannot separate" in r.text