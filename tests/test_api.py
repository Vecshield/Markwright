"""Tier-1 end-to-end API tests over real HTTP routes with a real miniature
scheme (ParityAdapter) and the real bundled corpus. Replaces the baseline
test_pipeline_and_api.py, whose two final assertions enshrined the calibration
deadlock as correct behaviour (inverted here — see docs/IMPLEMENTATION_LOG.md)."""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from .conftest import ADMIN, AUDITOR, OP, VERIFIER, bootstrap_parity_config, parity_marked_ids

SCHEMAS = Path(__file__).parent.parent / "schemas"
RESULT_SCHEMA = json.loads((SCHEMAS / "verification-result.json").read_text())
AUDIT_SCHEMA = json.loads((SCHEMAS / "audit-entry.json").read_text())


# ---- the end-to-end loop (the baseline deadlock, inverted) --------------------------------
def test_full_loop_calibrate_accept_activate_verify(api):
    client, a = api
    config_id = bootstrap_parity_config(client)

    res = client.post("/v1/verify", json={"token_ids": parity_marked_ids(300)}, headers=VERIFIER)
    assert res.status_code == 200
    body = res.json()
    assert body["band"] == "moderate_evidence"            # provisional floor keeps it below strong
    assert body["best_match"] == config_id
    assert "provisional_calibration" in body["caveats"]
    jsonschema.validate(body, RESULT_SCHEMA)              # the wire contract holds

    # unmarked: a real corpus passage
    from markwright.calibration import load_corpus
    doc = next(iter(load_corpus("gutenberg-en-v1")))
    res2 = client.post("/v1/verify", json={"token_ids": doc.ids}, headers=VERIFIER).json()
    assert res2["band"] in ("no_evidence", "weak_evidence")
    jsonschema.validate(res2, RESULT_SCHEMA)

    # 30 tokens: gated, no p-value anywhere
    res3 = client.post("/v1/verify", json={"token_ids": parity_marked_ids(30)}, headers=VERIFIER).json()
    assert res3["band"] == "insufficient_evidence" and res3["candidates"] == []

    # no boolean field anywhere in a result
    assert not any(isinstance(v, bool) for v in res.json().values())


def test_calibration_is_required_for_activation(api):
    client, _ = api
    config_id = bootstrap_parity_config(client, activate=False)
    # that helper accepted a calibration; a config WITHOUT one must refuse activation
    cfg2 = client.post("/v1/models/acme-llm/versions/4.1/configs",
                       json={"scheme_id": "parity-test"}, headers=OP).json()
    r = client.post(f"/v1/configs/{cfg2['config_id']}:activate", headers=OP)
    assert r.status_code == 409 and "calibration" in r.text
    # and the calibrated one activates fine
    assert client.post(f"/v1/configs/{config_id}:activate", headers=OP).status_code == 200


def test_uncalibrated_configs_are_never_scanned(api):
    client, _ = api
    bootstrap_parity_config(client)
    client.post("/v1/models/acme-llm/versions/4.1/configs",
                json={"scheme_id": "parity-test"}, headers=OP)      # draft, uncalibrated
    res = client.post("/v1/verify", json={"token_ids": parity_marked_ids(300)}, headers=VERIFIER).json()
    assert len(res["candidates"]) == 1                              # only the calibrated config


def test_verify_requires_exactly_one_input_kind(api):
    client, _ = api
    assert client.post("/v1/verify", json={}, headers=VERIFIER).status_code == 422
    assert client.post("/v1/verify", json={"text": "x", "token_ids": [1]}, headers=VERIFIER).status_code == 422


def test_null_models_survive_restart(api):
    client, a = api
    config_id = bootstrap_parity_config(client)
    a.STATE["null_models"] = {}                 # simulate process restart
    a._load_null_models()
    assert config_id in a.STATE["null_models"]
    res = client.post("/v1/verify", json={"token_ids": parity_marked_ids(300)}, headers=VERIFIER)
    assert res.json()["band"] == "moderate_evidence"


# ---- calibration routes -------------------------------------------------------------------
def test_calibration_rejects_unknown_corpus_and_tokenizer_mismatch(api):
    client, _ = api
    client.post("/v1/models", json={"model_id": "m2"}, headers=OP)
    client.post("/v1/models/m2/versions", json={"model_version": "1", "tokenizer_ref": "other-tok"}, headers=OP)
    cfg = client.post("/v1/models/m2/versions/1/configs", json={"scheme_id": "parity-test"}, headers=OP).json()
    r = client.post(f"/v1/configs/{cfg['config_id']}/calibrations",
                    json={"null_corpus_ids": ["no-such-corpus"]}, headers=OP)
    assert r.status_code == 422
    r = client.post(f"/v1/configs/{cfg['config_id']}/calibrations",
                    json={"null_corpus_ids": ["gutenberg-en-v1"]}, headers=OP)
    assert r.status_code == 422 and "tokenized with" in r.text


def test_calibration_report_shape_and_provisional_quality(api):
    client, _ = api
    client.post("/v1/models", json={"model_id": "acme-llm"}, headers=OP)
    client.post("/v1/models/acme-llm/versions",
                json={"model_version": "4.1", "tokenizer_ref": "gpt2"}, headers=OP)
    cfg = client.post("/v1/models/acme-llm/versions/4.1/configs",
                      json={"scheme_id": "parity-test"}, headers=OP).json()
    run = client.post(f"/v1/configs/{cfg['config_id']}/calibrations",
                      json={"null_corpus_ids": ["gutenberg-en-v1"]}, headers=OP).json()
    assert run["quality"] == "provisional"
    assert 0 < run["resolution_floor"] < 0.05
    assert run["n_docs"] == 900 and len(run["buckets"]) == 3
    assert any("PROVISIONAL" in n for n in run["notes"])
    assert "null_model" not in run                     # summaries never carry the big blob
    listed = client.get(f"/v1/configs/{cfg['config_id']}/calibrations", headers=OP).json()
    assert [r["run_id"] for r in listed] == [run["run_id"]] and listed[0]["status"] == "ran"


# ---- lifecycle state machine ---------------------------------------------------------------
def test_lifecycle_transitions(api):
    client, _ = api
    config_id = bootstrap_parity_config(client)        # now active
    assert client.post(f"/v1/configs/{config_id}:activate", headers=OP).status_code == 409   # already active
    assert client.post(f"/v1/configs/{config_id}:retire", headers=OP).status_code == 409     # active -/-> retired
    assert client.post(f"/v1/configs/{config_id}:deprecate", headers=OP).status_code == 200
    assert client.post(f"/v1/configs/{config_id}:activate", headers=OP).status_code == 409   # no resurrection
    assert client.post(f"/v1/configs/{config_id}:retire", headers=OP).status_code == 200
    assert client.post(f"/v1/configs/{config_id}:deprecate", headers=OP).status_code == 409  # terminal
    assert client.post(f"/v1/configs/{config_id}:nonsense", headers=OP).status_code == 404


def test_deprecated_config_verifies_with_historical_flag(api):
    client, _ = api
    config_id = bootstrap_parity_config(client)
    client.post(f"/v1/configs/{config_id}:deprecate", headers=OP)
    res = client.post("/v1/verify", json={"token_ids": parity_marked_ids(300)}, headers=VERIFIER).json()
    assert "historical_key" in res["caveats"] and res["band"] == "moderate_evidence"


# ---- generation profile --------------------------------------------------------------------
def test_generation_profile_issues_material_and_audits(api):
    client, a = api
    bootstrap_parity_config(client)
    r = client.get("/v1/models/acme-llm/versions/4.1/generation-profile", headers=OP)
    assert r.status_code == 200
    prof = r.json()
    assert prof["scheme_id"] == "parity-test" and len(bytes.fromhex(prof["sealed_material"])) == 32
    assert any(e["action"] == "key.handle_issue" for e in a.STATE["audit"].entries)
    # verifier may not obtain key material
    assert client.get("/v1/models/acme-llm/versions/4.1/generation-profile", headers=VERIFIER).status_code == 403


def test_generation_profile_refuses_deprecated_config(api):
    client, _ = api
    config_id = bootstrap_parity_config(client)
    client.post(f"/v1/configs/{config_id}:deprecate", headers=OP)
    r = client.get("/v1/models/acme-llm/versions/4.1/generation-profile",
                   params={"config_id": config_id}, headers=OP)
    assert r.status_code == 409 and "must not mark" in r.text


# ---- RBAC matrix ---------------------------------------------------------------------------
@pytest.mark.parametrize("headers,method,path,body,expected", [
    (None, "get", "/v1/models", None, 401),
    (VERIFIER, "get", "/v1/models", None, 200),
    (VERIFIER, "post", "/v1/models", {"model_id": "x-llm"}, 403),
    (AUDITOR, "post", "/v1/models", {"model_id": "x-llm"}, 403),
    (ADMIN, "post", "/v1/models", {"model_id": "x-llm"}, 201),
    (OP, "get", "/v1/audit", None, 403),
    (VERIFIER, "get", "/v1/audit", None, 403),
    (AUDITOR, "get", "/v1/audit", None, 200),
    (ADMIN, "get", "/v1/audit", None, 200),
    (VERIFIER, "get", "/v1/null-corpora", None, 200),
    (AUDITOR, "get", "/v1/policy", None, 200),
])
def test_rbac_matrix(api, headers, method, path, body, expected):
    client, _ = api
    kwargs = {"headers": headers} if headers else {}
    if body is not None:
        kwargs["json"] = body
    assert getattr(client, method)(path, **kwargs).status_code == expected


def test_verifier_sees_no_params_or_key_refs(api):
    client, _ = api
    config_id = bootstrap_parity_config(client)
    v = client.get(f"/v1/configs/{config_id}", headers=VERIFIER).json()
    assert "scheme_params" not in v and "key_ref" not in v
    full = client.get(f"/v1/configs/{config_id}", headers=OP).json()
    assert "scheme_params" in full and "key_ref" in full


def test_configs_listing_filters_and_role_views(api):
    """GET /v1/configs?model=&status= (design §8.1) — feeds the dashboard registry."""
    client, _ = api
    config_id = bootstrap_parity_config(client)
    rows = client.get("/v1/configs", params={"model": "acme-llm"}, headers=OP).json()
    assert [r["config_id"] for r in rows] == [config_id] and "key_ref" in rows[0]
    rows_v = client.get("/v1/configs", headers=VERIFIER).json()
    assert len(rows_v) == 1 and "key_ref" not in rows_v[0] and "scheme_params" not in rows_v[0]
    assert client.get("/v1/configs", params={"status": "draft"}, headers=OP).json() == []
    assert client.get("/v1/configs", params={"status": "active"}, headers=OP).json() != []


def test_denied_calls_are_audited_and_chain_valid(api):
    client, a = api
    client.post("/v1/models", json={"model_id": "nope"}, headers=VERIFIER)
    audit = client.get("/v1/audit", headers=AUDITOR).json()
    assert any(e["action"] == "auth.denied" for e in audit["entries"])
    assert audit["head"] == audit["entries"][-1]["entry_hash"]
    for e in audit["entries"]:
        jsonschema.validate(e, AUDIT_SCHEMA)


# ---- scheme catalogue + demo samples (UI support) ------------------------------------------
def test_scheme_catalogue_describes_installed_and_absent_schemes(api):
    client, _ = api
    cat = client.get("/v1/schemes/catalogue", headers=VERIFIER)
    assert cat.status_code == 200
    by_id = {e["scheme_id"]: e for e in cat.json()}
    assert "synthid-text" in by_id and "unigram" in by_id
    sid = by_id["synthid-text"]
    assert sid["installed"] is True and sid["scheme_version"]
    # an honest entry explains mechanism AND limits, and names its source
    for field in ("how_it_marks", "how_it_detects", "statistic", "key_material", "citation"):
        assert sid[field]
    assert sid["limits"] and sid["strengths"]
    # a scheme we do not ship is described but flagged
    assert by_id["unigram"]["installed"] is False and "capabilities" not in by_id["unigram"]


def test_schemes_endpoint_carries_explanations(api):
    client, _ = api
    entries = client.get("/v1/schemes", headers=OP).json()
    synthid = next(e for e in entries if e["scheme_id"] == "synthid-text")
    assert synthid["explanation"]["title"].startswith("SynthID")
    # the test-only parity scheme has no explanation, and that is not an error
    parity = next(e for e in entries if e["scheme_id"] == "parity-test")
    assert parity["explanation"] is None


def test_demo_samples_are_generated_on_this_deployment(api):
    """The Verify presets must be real inputs for THIS deployment, with the
    marked one carrying this deployment's own key."""
    client, _ = api
    r = client.get("/v1/samples", headers=VERIFIER)
    assert r.status_code == 200
    body = r.json()
    ids = {s["id"] for s in body["samples"]}
    assert {"human", "short"} <= ids
    # no active config yet -> no marked sample, and the reason is stated rather
    # than the endpoint silently returning fewer samples
    assert "marked" not in ids and any("no active" in n for n in body["notes"])
    short = next(s for s in body["samples"] if s["id"] == "short")
    assert len(short["token_ids"]) == 30 and short["expect"] == "insufficient evidence"
    # readable prose wherever possible: a person should not be shown token ids
    for s in body["samples"]:
        assert s["kind"] in ("text", "token_ids")
        if s["kind"] == "text":
            assert s["text"] and not s["text"].startswith("[")

    # after onboarding, the marked sample appears and actually verifies
    bootstrap_parity_config(client)
    body2 = client.get("/v1/samples", headers=VERIFIER).json()
    marked = next(s for s in body2["samples"] if s["id"] == "marked")
    res = client.post("/v1/verify", json={"token_ids": marked["token_ids"]}, headers=VERIFIER).json()
    assert res["band"] in ("moderate_evidence", "strong_evidence")
    # and the samples behave as the labels promise
    human = next(s for s in body2["samples"] if s["id"] == "human")
    assert client.post("/v1/verify", json={"token_ids": human["token_ids"]},
                       headers=VERIFIER).json()["band"] in ("no_evidence", "weak_evidence")
    assert client.post("/v1/verify", json={"token_ids": short["token_ids"]},
                       headers=VERIFIER).json()["band"] == "insufficient_evidence"
