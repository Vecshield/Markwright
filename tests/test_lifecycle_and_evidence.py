"""Tier-1 tests for Milestone B phase 4 (key lifecycle, persistent signed
audit, detector publish, batch verify, policy, immutability) and phase 5
(offline bundles). All through real HTTP routes with the real ParityAdapter
mini-scheme — no mocks, no torch."""
from __future__ import annotations

import json

import pytest

from markwright.audit import verify_bundle

from .conftest import ADMIN, AUDITOR, OP, VERIFIER, bootstrap_parity_config, parity_marked_ids


# ---- 4.1 key lifecycle ----------------------------------------------------------------------
def test_rotation_moves_configs_and_keeps_old_key_verifying(api):
    client, a = api
    old_cfg = bootstrap_parity_config(client)
    key_id = client.get(f"/v1/configs/{old_cfg}", headers=OP).json()["key_ref"]

    r = client.post(f"/v1/keys/{key_id}:rotate", headers=OP)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["old_key"]["state"] == "verify-only" and out["old_key"]["generate_until"]
    assert out["new_key"]["key_version"] == 2 and out["new_key"]["state"] == "active"
    assert out["new_key"]["lineage_id"] == out["old_key"]["lineage_id"]
    new_cfg = out["configs"][0]["new"]

    # old config deprecated, new one active, calibration inherited
    assert client.get(f"/v1/configs/{old_cfg}", headers=OP).json()["status"] == "deprecated"
    nc = client.get(f"/v1/configs/{new_cfg}", headers=OP).json()
    assert nc["status"] == "active" and nc["calibration_ref"] and nc["supersedes"] == old_cfg

    # marked text matches BOTH configs (same lineage-derived parity stat); the
    # Bonferroni correction covers the two candidates and old key still scans
    res = client.post("/v1/verify", json={"token_ids": parity_marked_ids(300)}, headers=VERIFIER).json()
    assert {c["config_id"] for c in res["candidates"]} == {old_cfg, new_cfg}
    assert "historical_key" in res["caveats"] and "multiple_candidates" in res["caveats"]
    assert res["band"] == "moderate_evidence"

    # generation-profile now issues ONLY the new config
    prof = client.get("/v1/models/acme-llm/versions/4.1/generation-profile", headers=OP).json()
    assert prof["config_id"] == new_cfg and prof["key_version"] == 2


def test_rotated_old_key_cannot_issue_generation_profiles(api):
    client, _ = api
    cfg = bootstrap_parity_config(client)
    key_id = client.get(f"/v1/configs/{cfg}", headers=OP).json()["key_ref"]
    client.post(f"/v1/keys/{key_id}:rotate", headers=OP)
    r = client.get("/v1/models/acme-llm/versions/4.1/generation-profile",
                   params={"config_id": cfg}, headers=OP)
    assert r.status_code == 409   # deprecated config must not mark


def test_revoke_stops_marking_keeps_flagged_verification(api):
    client, _ = api
    cfg = bootstrap_parity_config(client)
    key_id = client.get(f"/v1/configs/{cfg}", headers=OP).json()["key_ref"]

    assert client.post(f"/v1/keys/{key_id}:revoke", json={"reason": "x"}, headers=OP).status_code == 403
    r = client.post(f"/v1/keys/{key_id}:revoke", json={"reason": "laptop stolen"}, headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["state"] == "revoked" and r.json()["revocation_reason"] == "laptop stolen"

    res = client.post("/v1/verify", json={"token_ids": parity_marked_ids(300)}, headers=VERIFIER).json()
    assert res["band"] == "moderate_evidence" and "historical_key" in res["caveats"]
    assert client.get("/v1/models/acme-llm/versions/4.1/generation-profile",
                      headers=OP).status_code == 404   # no active config remains
    # double revoke → 409
    assert client.post(f"/v1/keys/{key_id}:revoke", json={"reason": "again"}, headers=ADMIN).status_code == 409


def test_destroy_requires_elapsed_verify_until(api):
    client, _ = api
    cfg = bootstrap_parity_config(client)
    key_id = client.get(f"/v1/configs/{cfg}", headers=OP).json()["key_ref"]
    r = client.post(f"/v1/keys/{key_id}:destroy", headers=ADMIN)
    assert r.status_code == 409 and "verify_until" in r.text
    assert client.post(f"/v1/keys/{key_id}:destroy", headers=OP).status_code == 403


def test_key_rbac_and_purpose_separation(api):
    client, _ = api
    bootstrap_parity_config(client)
    assert client.get("/v1/keys", headers=AUDITOR).status_code == 200
    assert client.get("/v1/keys", headers=VERIFIER).status_code == 403
    keys = client.get("/v1/keys", headers=AUDITOR).json()
    purposes = {k["purpose"] for k in keys}
    assert purposes == {"generation", "signing"}         # deployment identity is a separate record
    assert not any("secret" in k or "material" in json.dumps(k).lower() for k in keys)
    # a config may not reference the signing key
    signing = next(k for k in keys if k["purpose"] == "signing")
    r = client.post("/v1/models/acme-llm/versions/4.1/configs",
                    json={"scheme_id": "parity-test", "key": signing["key_id"]}, headers=OP)
    assert r.status_code == 422


# ---- 4.3 persistent signed audit ------------------------------------------------------------
def test_audit_survives_restart_and_anchors_verify(api):
    client, a = api
    bootstrap_parity_config(client)
    n_before = len(a.STATE["audit"].entries)
    assert n_before > 5
    a._init_audit()                      # simulate process restart: reload from sqlite
    assert len(a.STATE["audit"].entries) == n_before
    assert client.get("/v1/audit", headers=AUDITOR).json()["head"] == a.STATE["audit"].head
    # appends continue the same chain after reload
    client.post("/v1/models", json={"model_id": "post-restart"}, headers=OP)
    from markwright.audit import AuditChain
    ok, msg = AuditChain.verify(a.STATE["audit"].entries)
    assert ok, msg


def test_audit_export_bundle_verifies_and_detects_tampering(api):
    client, a = api
    bootstrap_parity_config(client)
    r = client.post("/v1/audit/export", headers=AUDITOR)
    assert r.status_code == 200 and r.headers["content-type"] == "application/gzip"
    ok, msg = verify_bundle(r.content)
    assert ok, msg
    # out-of-band public key path
    pub = client.get("/v1/audit/public-key", headers=AUDITOR).json()["public_key_hex"]
    assert verify_bundle(r.content, pub)[0]
    # a different deployment's key must NOT validate the bundle
    from markwright.audit import new_signing_key
    _, other_pub = new_signing_key()
    ok2, msg2 = verify_bundle(r.content, other_pub.hex())
    assert not ok2 and "signature" in msg2
    # tamper with an entry inside the tar → chain/entry verification fails
    import io
    import tarfile
    with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as tar:
        files = {m.name: tar.extractfile(m).read() for m in tar.getmembers()}
    lines = files["entries.jsonl"].decode().splitlines()
    e0 = json.loads(lines[0])
    e0["actor"]["principal"] = "mallory"
    lines[0] = json.dumps(e0, sort_keys=True)
    files["entries.jsonl"] = "\n".join(lines).encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    ok3, msg3 = verify_bundle(buf.getvalue())
    assert not ok3 and ("hash mismatch" in msg3 or "chain break" in msg3)


# ---- 4.5 detector publish -------------------------------------------------------------------
def test_detector_publish_sets_disclosure_and_spoofing_caveat(api):
    client, _ = api
    cfg = bootstrap_parity_config(client)
    r = client.post(f"/v1/configs/{cfg}/detector:publish", headers=OP)
    assert r.status_code == 200, r.text
    assert r.json()["labels"]["disclosure"] == "public-detector"
    det = client.get(f"/v1/detectors/{cfg}", headers=VERIFIER)   # public artifact, any role
    assert det.status_code == 200 and det.json()["scheme_id"] == "parity-test"
    res = client.post("/v1/verify", json={"token_ids": parity_marked_ids(300)}, headers=VERIFIER).json()
    assert "spoofing_not_excluded" in res["caveats"]


def test_detector_404_when_unpublished(api):
    client, _ = api
    cfg = bootstrap_parity_config(client)
    assert client.get(f"/v1/detectors/{cfg}", headers=VERIFIER).status_code == 404


# ---- 4.6 batch, results, policy, health ------------------------------------------------------
def test_batch_reports_expected_false_positives(api):
    client, _ = api
    bootstrap_parity_config(client)
    from markwright.calibration import load_corpus
    docs = []
    it = iter(load_corpus("gutenberg-en-v1"))
    for _ in range(5):
        docs.append(list(next(it).ids))
    batch = docs + [parity_marked_ids(300, seed=s) for s in range(3)]
    r = client.post("/v1/verify/batch", json={"token_ids": batch}, headers=VERIFIER)
    assert r.status_code == 200, r.text
    s = r.json()["summary"]
    assert s["n"] == 8
    assert s["positives_at_or_above_moderate"] == 3           # the marked three
    assert s["expected_false_positives"] == pytest.approx(8 * 0.01)
    assert "expected by chance" in s["note"]
    assert all(res["request_id"] == r.json()["batch_id"] for res in r.json()["results"])
    # exactly one input kind
    assert client.post("/v1/verify/batch", json={"texts": ["x"], "token_ids": [[1]]},
                       headers=VERIFIER).status_code == 422


def test_results_listing_scoped_by_role(api):
    client, _ = api
    bootstrap_parity_config(client)
    client.post("/v1/verify", json={"token_ids": parity_marked_ids(300)}, headers=VERIFIER)
    client.post("/v1/verify", json={"token_ids": parity_marked_ids(300, seed=1)}, headers=OP)
    assert len(client.get("/v1/verify/results", headers=VERIFIER).json()) == 1   # own only
    both = client.get("/v1/verify/results", headers=AUDITOR).json()
    assert len(both) == 2
    # metadata only: no payload blob, no raw text — a digest (hex) is all that remains
    for r in both:
        assert "payload" not in r and "text" not in r
        assert r["text_digest"] is None or all(c in "0123456789abcdef" for c in r["text_digest"])
    flagged = client.get("/v1/verify/results", params={"band": "moderate_evidence", "model": "acme-llm"},
                         headers=AUDITOR).json()
    assert len(flagged) == 2 and all(r["model_id"] == "acme-llm" for r in flagged)


def test_policy_update_is_admin_only_and_audited(api):
    client, a = api
    assert client.put("/v1/policy", json={"fpr_target": 1e-3}, headers=OP).status_code == 403
    r = client.put("/v1/policy", json={"fpr_target": 1e-3}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["fpr_target"] == 1e-3
    assert client.put("/v1/policy", json={"fpr_target": 0.5}, headers=ADMIN).status_code == 422
    assert any(e["action"] == "policy.change" for e in a.STATE["audit"].entries)


def test_readyz_and_metrics(api):
    client, _ = api
    bootstrap_parity_config(client)
    client.post("/v1/verify", json={"token_ids": parity_marked_ids(300)}, headers=VERIFIER)
    r = client.get("/readyz")
    assert r.status_code == 200 and r.json()["calibrated_configs"] == 1
    m = client.get("/metrics").text
    assert 'markwright_results_total{band="moderate_evidence"} 1' in m
    assert 'markwright_configs{status="active"} 1' in m


# ---- 4.7 immutability as a constraint --------------------------------------------------------
def test_active_config_is_immutable_at_the_db_level(api):
    client, a = api
    cfg = bootstrap_parity_config(client)
    import sqlite3
    db = a.STATE["registry"].db
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("UPDATE configs SET scheme_params='{}' WHERE config_id=?", (cfg,))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("UPDATE configs SET key_ref='evil' WHERE config_id=?", (cfg,))
    # lifecycle columns stay mutable
    db.execute("UPDATE configs SET status='deprecated' WHERE config_id=?", (cfg,))
    db.rollback()


# ---- phase 5: offline bundles ----------------------------------------------------------------
def test_bundle_export_and_offline_verify(api, tmp_path):
    client, _ = api
    cfg = bootstrap_parity_config(client)
    client.post(f"/v1/configs/{cfg}/detector:publish", headers=OP)
    # a second calibrated config WITHOUT a published detector → must be skipped, not dropped
    cfg2 = client.post("/v1/models/acme-llm/versions/4.1/configs",
                       json={"scheme_id": "parity-test"}, headers=OP).json()["config_id"]
    run = client.post(f"/v1/configs/{cfg2}/calibrations",
                      json={"null_corpus_ids": ["gutenberg-en-v1"]}, headers=OP).json()
    client.post(f"/v1/calibrations/{run['run_id']}:accept", headers=OP)
    client.post(f"/v1/configs/{cfg2}:activate", headers=OP)

    r = client.get("/v1/bundle", params={"model": "acme-llm"}, headers=OP)
    assert r.status_code == 200
    assert client.get("/v1/bundle", headers=VERIFIER).status_code == 403   # operator exports
    path = tmp_path / "acme.bundle"
    path.write_bytes(r.content)

    from markwright.verify.offline import verify_offline
    result, loaded = verify_offline(path, parity_marked_ids(300))
    assert [s["config_id"] for s in loaded.skipped] == [cfg2]
    assert "detector" in loaded.skipped[0]["reason"]
    assert result["band"] == "moderate_evidence"
    assert result["engine"]["mode"] == "offline"
    assert {"spoofing_not_excluded", "provisional_calibration"} <= set(result["caveats"])
    # offline verifier may only tighten the FPR target
    tight, _ = verify_offline(path, parity_marked_ids(300), fpr_target=1e-3)
    assert tight["fpr_target"] == pytest.approx(1e-3)
    assert tight["band"] == "weak_evidence"               # tighter than the floor → capped