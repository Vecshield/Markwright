"""Integration tests for the RegistryStore on a REAL PostgreSQL (marker:
network — excluded from CI tiers 1/2):

    docker run -d --rm --name mw-postgres -p 5433:5432 \\
        -e POSTGRES_USER=markwright -e POSTGRES_PASSWORD=test \\
        -e POSTGRES_DB=markwright postgres:16-alpine

Same store class, same methods, same tests-shape as SQLite — that IS the
claim (design §9: 'Postgres in production, SQLite for single-node/offline,
behind the same methods'). Includes the plpgsql immutability trigger and the
full HTTP control-plane loop with the registry and audit chain on PG.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.network

PG_URL = os.environ.get("MARKWRIGHT_PG_URL",
                        "postgresql+psycopg://markwright:test@localhost:5433/markwright")


def _pg_up() -> bool:
    try:
        from sqlalchemy import create_engine, text
        with create_engine(PG_URL).connect() as cx:
            cx.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


if not _pg_up():
    pytest.skip("no PostgreSQL on :5433", allow_module_level=True)

from markwright.registry.store import ImmutableConfigError, RegistryStore  # noqa: E402

TABLES = ("audit_log", "results", "calibrations", "configs", "keys", "model_versions", "models")


def _clean(store: RegistryStore) -> None:
    """Isolate runs — the container keeps state. audit_log belongs to the
    AuditChain, which may not have been constructed on PG yet."""
    store.db.execute("CREATE TABLE IF NOT EXISTS audit_log (seq INTEGER PRIMARY KEY, entry TEXT)")
    for t in TABLES:
        store.db.execute(f"DELETE FROM {t}")


@pytest.fixture()
def pg_store():
    store = RegistryStore(PG_URL)
    _clean(store)
    return store


def test_store_roundtrip_json_and_lifecycle(pg_store):
    reg = pg_store
    reg.add_model("pg-llm", "PG", "op")
    assert reg.get_model("pg-llm")["display_name"] == "PG"
    reg.add_version("pg-llm", "1", "gpt2")
    assert reg.get_model_version("pg-llm", "1")["tokenizer_ref"] == "gpt2"
    cfg = reg.create_config("pg-llm", "1", "parity-test", "0", {"min_tokens": 50, "nested": {"a": [1, 2]}},
                            "gpt2", "key-1", "op", labels={"env": "test"})
    got = reg.get_config(cfg["config_id"])
    assert got["scheme_params"]["nested"] == {"a": [1, 2]} and got["labels"] == {"env": "test"}
    assert got["status"] == "draft" and got["config_version"] == 1
    # supersedes chains across versions
    cfg2 = reg.create_config("pg-llm", "1", "parity-test", "0", {}, "gpt2", "key-1", "op")
    assert cfg2["config_version"] == 2 and cfg2["supersedes"] == cfg["config_id"]
    # lifecycle rules identical to sqlite
    with pytest.raises(ValueError, match="calibration"):
        reg.transition(cfg["config_id"], "active")
    with pytest.raises(ValueError, match="illegal"):
        reg.transition(cfg["config_id"], "deprecated")


def test_immutability_trigger_fires_in_plpgsql(pg_store):
    """Dialect nuance, on purpose: SQLite's `UPDATE OF col` trigger fires when
    the column is MENTIONED; the plpgsql function fires when a value actually
    CHANGES (IS DISTINCT FROM) — no-op updates pass, real mutations abort."""
    reg = pg_store
    reg.add_model("pg-llm", "", "op")
    reg.add_version("pg-llm", "1", "gpt2")
    cfg = reg.create_config("pg-llm", "1", "parity-test", "0", {"gamma": 0.25}, "gpt2", "key-1", "op")
    report = {"run_id": "run-pg-1", "created_at": "2026-10-28T00:00:00+00:00",
              "quality": "provisional", "min_detectable_tokens": None,
              "tokenizer_hash": None, "null_model": {"statistic_name": "x", "buckets": []}}
    reg.save_calibration(cfg["config_id"], report, "op")
    reg.accept_calibration("run-pg-1")
    reg.transition(cfg["config_id"], "active")
    with pytest.raises(ImmutableConfigError, match="immutable"):
        reg.db.execute("UPDATE configs SET scheme_params='{}' WHERE config_id=:id",
                       {"id": cfg["config_id"]})
    with pytest.raises(ImmutableConfigError, match="immutable"):
        reg.db.execute("UPDATE configs SET key_ref='evil' WHERE config_id=:id",
                       {"id": cfg["config_id"]})
    # mutable columns still update, and the failed statements rolled back
    reg.transition(cfg["config_id"], "deprecated")
    assert reg.get_config(cfg["config_id"])["key_ref"] == "key-1"
    # draft configs stay editable (the trigger checks OLD.status)
    draft = reg.create_config("pg-llm", "1", "parity-test", "0", {}, "gpt2", "key-1", "op")
    reg.db.execute("UPDATE configs SET scheme_params='{\"x\": 1}' WHERE config_id=:id",
                   {"id": draft["config_id"]})
    assert reg.get_config(draft["config_id"])["scheme_params"] == {"x": 1}


def test_accepted_null_models_join_and_results_filters(pg_store):
    reg = pg_store
    reg.add_model("pg-llm", "", "op")
    reg.add_version("pg-llm", "1", "gpt2")
    cfg = reg.create_config("pg-llm", "1", "parity-test", "0", {}, "gpt2", "key-1", "op")
    report = {"run_id": "run-pg-2", "created_at": "2026-10-28T00:00:00+00:00",
              "quality": "provisional", "min_detectable_tokens": 180, "tokenizer_hash": "abc",
              "null_model": {"statistic_name": "even_fraction", "buckets": [], "quality": "provisional"}}
    reg.save_calibration(cfg["config_id"], report, "op")
    reg.accept_calibration("run-pg-2")
    hydrated = reg.accepted_null_models()
    assert hydrated[cfg["config_id"]]["statistic_name"] == "even_fraction"
    vd = reg.get_config(cfg["config_id"])["verification_defaults"]
    assert vd["min_detectable_tokens"] == 180 and vd["tokenizer_hash"] == "abc"

    reg.save_result({"result_id": "r1", "requested_at": "2026-10-28T01:00:00+00:00",
                     "requested_by": "ver", "text_digest": None, "token_count": 300,
                     "fpr_target": 0.01, "band": "moderate_evidence",
                     "best_match": cfg["config_id"]})
    assert reg.list_results(model_id="pg-llm")[0]["result_id"] == "r1"
    assert reg.list_results(band="no_evidence") == []
    assert reg.list_results(requested_by="someone-else") == []


def test_full_control_plane_on_postgres(api, tmp_path):
    """The parity HTTP loop with registry AND audit chain on PG; audit survives
    a simulated restart; evidence bundle verifies."""
    from markwright.audit import AuditChain, verify_bundle

    from .conftest import OP, VERIFIER, bootstrap_parity_config, parity_marked_ids
    client, a = api
    pg = RegistryStore(PG_URL)
    _clean(pg)
    a.STATE["registry"] = pg
    a.STATE["null_models"] = {}
    a._init_audit()

    config_id = bootstrap_parity_config(client)
    res = client.post("/v1/verify", json={"token_ids": parity_marked_ids(300)}, headers=VERIFIER).json()
    assert res["band"] == "moderate_evidence" and res["best_match"] == config_id

    # restart: a fresh store + chain hydrate from PG
    a.STATE["registry"] = RegistryStore(PG_URL)
    a.STATE["null_models"] = {}
    a._load_null_models()
    a._init_audit()
    assert config_id in a.STATE["null_models"]
    assert len(a.STATE["audit"].entries) > 5
    ok, msg = AuditChain.verify(a.STATE["audit"].entries)
    assert ok, msg
    res2 = client.post("/v1/verify", json={"token_ids": parity_marked_ids(300, seed=1)}, headers=VERIFIER).json()
    assert res2["band"] == "moderate_evidence"
    export = client.post("/v1/audit/export", headers={"Authorization": "Bearer auditor-token"})
    assert verify_bundle(export.content)[0]
    # the registry rows really are in PG, not sqlite
    assert pg.db.dialect == "postgresql"
    assert client.get(f"/v1/configs/{config_id}", headers=OP).json()["status"] == "active"