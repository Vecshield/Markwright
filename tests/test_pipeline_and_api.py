"""End-to-end pipeline and API tests with a stub adapter, so the honesty rules are
tested without torch. The SynthID adapter itself is exercised in tests/test_synthid.py
when transformers is installed."""
import numpy as np
import pytest
from fastapi.testclient import TestClient

from markwright.schemes.base import Capabilities, Detection, KeyHandle
from markwright.stats.null_model import NullModel, fit_bucket
from markwright.verify import Candidate, verify


class StubAdapter:
    scheme_id, scheme_version, capabilities = "stub", "0", Capabilities()

    def __init__(self, value: float):
        self.value = value

    def detect(self, text, params, key, tokenizer_ref):
        n = len(text.split())
        return Detection("mean_g", self.value, n)


def _nm(seed=0):
    rng = np.random.default_rng(seed)
    return NullModel("mean_g", [fit_bucket(rng.normal(0.5, 0.05, 20000).tolist(), 200)])


def _cand(value, cid="c1", min_tokens=50, flags=()):
    return Candidate(cid, "stub", StubAdapter(value), {}, KeyHandle("k", 1, "local", "r", b"x"), "tok", _nm(), min_tokens, 180, 1, list(flags))


def words(n):
    return " ".join(["word"] * n)


def test_short_text_is_insufficient_not_a_number():
    r = verify(words(20), [_cand(0.9)], 1e-4, count_tokens=lambda t, _: len(t.split()))
    assert r["band"] == "insufficient_evidence" and r["best_match"] is None and r["candidates"] == []
    assert "short_text" in r["caveats"] and "result_is_probabilistic" in r["caveats"]


def test_strong_evidence_has_interval_and_caveats():
    r = verify(words(200), [_cand(0.85)], 1e-4, count_tokens=lambda t, _: len(t.split()))
    c = r["candidates"][0]
    assert r["band"] == "strong_evidence"
    lo, hi = c["ci_95"]
    assert 0 < lo <= hi <= 1 and c["p_value_adjusted"] <= 1e-6
    assert "possible_paraphrase" in r["caveats"]


def test_bonferroni_over_candidates_and_flags_propagate():
    cands = [_cand(0.5, "c1"), _cand(0.5, "c2"), _cand(0.5, "c3", flags=("spoofing_not_excluded",))]
    r = verify(words(200), cands, 1e-4, count_tokens=lambda t, _: len(t.split()))
    for c in r["candidates"]:
        assert abs(c["p_value_adjusted"] - min(1.0, 3 * c["p_value"])) < 1e-12
    assert "multiple_candidates" in r["caveats"] and "spoofing_not_excluded" in r["caveats"]
    assert r["band"] == "no_evidence" and r["best_match"] is None


def test_result_is_not_a_boolean():
    r = verify(words(200), [_cand(0.9)], 1e-4, count_tokens=lambda t, _: len(t.split()))
    with pytest.raises(TypeError):
        bool(r)


def test_api_rbac_and_no_boolean_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKWRIGHT_VAULT", str(tmp_path / "v.json"))
    from markwright.api import app as a
    client = TestClient(a.app)
    assert client.get("/v1/models").status_code == 401
    assert client.get("/v1/models", headers={"Authorization": "Bearer verifier-token"}).status_code == 200
    r = client.post("/v1/models", json={"model_id": "acme-llm"}, headers={"Authorization": "Bearer verifier-token"})
    assert r.status_code == 403
    op = {"Authorization": "Bearer operator-token"}
    assert client.post("/v1/models", json={"model_id": "acme-llm"}, headers=op).status_code == 201
    assert client.post("/v1/models/acme-llm/versions", json={"model_version": "4.1", "tokenizer_ref": "gpt2"}, headers=op).status_code == 201
    r = client.post("/v1/models/acme-llm/versions/4.1/configs", json={"scheme_id": "synthid-text", "scheme_params": {"ngram_len": 5}}, headers=op)
    assert r.status_code == 201, r.text
    cfg = r.json()
    assert cfg["status"] == "draft" and cfg["scheme_params"]["num_keys"] == 30
    # cannot activate without an accepted calibration
    assert client.post(f"/v1/configs/{cfg['config_id']}:activate", headers=op).status_code == 409
    # verifier sees no params or key refs
    v = client.get(f"/v1/configs/{cfg['config_id']}", headers={"Authorization": "Bearer verifier-token"}).json()
    assert "scheme_params" not in v and "key_ref" not in v
    res = client.post("/v1/verify", json={"text": "hello " * 100}, headers={"Authorization": "Bearer verifier-token"}).json()
    assert res["band"] == "insufficient_evidence"          # uncalibrated configs are never scanned
    assert not any(isinstance(val, bool) for val in res.values())
    audit = client.get("/v1/audit", headers={"Authorization": "Bearer auditor-token"}).json()
    assert any(e["action"] == "auth.denied" for e in audit["entries"]) and audit["head"] == audit["entries"][-1]["entry_hash"]
