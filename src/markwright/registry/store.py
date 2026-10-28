"""Registry store. MVP: SQLite via stdlib; v1 swaps in SQLAlchemy/Postgres behind
the same methods. Config versions are immutable once active."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS models (
  model_id TEXT PRIMARY KEY, display_name TEXT, created_at TEXT, created_by TEXT);
CREATE TABLE IF NOT EXISTS model_versions (
  model_id TEXT, model_version TEXT, tokenizer_ref TEXT, model_fingerprint TEXT, created_at TEXT,
  PRIMARY KEY (model_id, model_version));
CREATE TABLE IF NOT EXISTS configs (
  config_id TEXT PRIMARY KEY, model_id TEXT, model_version TEXT, config_version INTEGER,
  supersedes TEXT, scheme_id TEXT, scheme_version TEXT, scheme_params TEXT, tokenizer_ref TEXT,
  key_ref TEXT, detector_ref TEXT, calibration_ref TEXT, verification_defaults TEXT,
  status TEXT, generate_from TEXT, generate_until TEXT, verify_until TEXT, labels TEXT,
  created_at TEXT, created_by TEXT);
CREATE TABLE IF NOT EXISTS keys (
  key_id TEXT PRIMARY KEY, purpose TEXT, owner_model_id TEXT, backend TEXT, backend_ref TEXT,
  key_version INTEGER, lineage_id TEXT, derivation TEXT, custody TEXT, state TEXT,
  valid_from TEXT, generate_until TEXT, verify_until TEXT, rotation_policy TEXT,
  public_detector_ref TEXT, created_at TEXT, created_by TEXT, revoked_at TEXT, revocation_reason TEXT);
CREATE TABLE IF NOT EXISTS results (
  result_id TEXT PRIMARY KEY, requested_at TEXT, requested_by TEXT, text_digest TEXT,
  token_count INTEGER, fpr_target REAL, band TEXT, best_match TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS calibrations (
  run_id TEXT PRIMARY KEY, config_id TEXT, created_at TEXT, created_by TEXT,
  status TEXT, quality TEXT, report TEXT);
-- Config immutability as a DB constraint, not a convention (design §4, POC 4.7):
-- identity-defining columns may not change once a config leaves draft. Lifecycle
-- columns (status, dates), calibration_ref/verification_defaults, detector_ref
-- and labels stay mutable; rotation creates NEW rows instead of updating.
CREATE TRIGGER IF NOT EXISTS configs_immutable
BEFORE UPDATE OF model_id, model_version, config_version, supersedes,
                 scheme_id, scheme_version, scheme_params, tokenizer_ref, key_ref
ON configs WHEN old.status != 'draft'
BEGIN
  SELECT RAISE(ABORT, 'config is immutable once active (create a new config version instead)');
END;
"""

TRANSITIONS = {"draft": {"active", "retired"}, "active": {"deprecated"}, "deprecated": {"retired"}, "retired": set()}


def _now() -> str:
    return datetime.now(UTC).isoformat()


class RegistryStore:
    def __init__(self, path: str | Path = ":memory:"):
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # models ---------------------------------------------------------------------------
    def add_model(self, model_id: str, display_name: str, by: str) -> dict:
        self.db.execute("INSERT INTO models VALUES (?,?,?,?)", (model_id, display_name, _now(), by))
        self.db.commit()
        return self.get_model(model_id)

    def get_model(self, model_id: str) -> dict | None:
        r = self.db.execute("SELECT * FROM models WHERE model_id=?", (model_id,)).fetchone()
        return dict(r) if r else None

    def list_models(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM models ORDER BY created_at")]

    def add_version(self, model_id: str, version: str, tokenizer_ref: str, fingerprint: str | None = None) -> dict:
        self.db.execute("INSERT INTO model_versions VALUES (?,?,?,?,?)", (model_id, version, tokenizer_ref, fingerprint, _now()))
        self.db.commit()
        return {"model_id": model_id, "model_version": version, "tokenizer_ref": tokenizer_ref}

    # configs --------------------------------------------------------------------------
    def create_config(self, model_id: str, model_version: str, scheme_id: str, scheme_version: str,
                      scheme_params: dict, tokenizer_ref: str, key_ref: str, by: str,
                      verification_defaults: dict | None = None, labels: dict | None = None) -> dict:
        prev = self.db.execute(
            "SELECT config_id, MAX(config_version) v FROM configs WHERE model_id=? AND model_version=?",
            (model_id, model_version)).fetchone()
        cv = (prev["v"] or 0) + 1
        cfg = {
            "config_id": str(uuid.uuid4()), "model_id": model_id, "model_version": model_version,
            "config_version": cv, "supersedes": prev["config_id"] if prev["v"] else None,
            "scheme_id": scheme_id, "scheme_version": scheme_version, "scheme_params": scheme_params,
            "tokenizer_ref": tokenizer_ref, "key_ref": key_ref, "detector_ref": None, "calibration_ref": None,
            "verification_defaults": verification_defaults or {"fpr_target": 1e-4, "min_tokens": 50, "min_detectable_tokens": None},
            "status": "draft", "generate_from": None, "generate_until": None, "verify_until": None,
            "labels": labels or {}, "created_at": _now(), "created_by": by,
        }
        self.db.execute(
            "INSERT INTO configs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cfg["config_id"], model_id, model_version, cv, cfg["supersedes"], scheme_id, scheme_version,
             json.dumps(scheme_params), tokenizer_ref, key_ref, None, None, json.dumps(cfg["verification_defaults"]),
             "draft", None, None, None, json.dumps(cfg["labels"]), cfg["created_at"], by))
        self.db.commit()
        return cfg

    def get_config(self, config_id: str) -> dict | None:
        r = self.db.execute("SELECT * FROM configs WHERE config_id=?", (config_id,)).fetchone()
        return self._cfg(r) if r else None

    def list_configs(self, model_id: str | None = None, statuses: tuple[str, ...] | None = None) -> list[dict]:
        q, args = "SELECT * FROM configs WHERE 1=1", []
        if model_id:
            q += " AND model_id=?"
            args.append(model_id)
        if statuses:
            q += f" AND status IN ({','.join('?' * len(statuses))})"
            args += list(statuses)
        return [self._cfg(r) for r in self.db.execute(q + " ORDER BY created_at", args)]

    def transition(self, config_id: str, new_status: str) -> dict:
        cfg = self.get_config(config_id)
        if cfg is None:
            raise KeyError(config_id)
        if new_status not in TRANSITIONS[cfg["status"]]:
            raise ValueError(f"illegal transition {cfg['status']} -> {new_status}")
        if new_status == "active" and not cfg["calibration_ref"]:
            raise ValueError("a config needs an accepted calibration before it can be activated")
        cols = {"status": new_status}
        if new_status == "active":
            cols["generate_from"] = _now()
        if new_status in ("deprecated", "retired"):
            cols["generate_until"] = _now()
        sets = ", ".join(f"{k}=?" for k in cols)
        self.db.execute(f"UPDATE configs SET {sets} WHERE config_id=?", (*cols.values(), config_id))
        self.db.commit()
        return self.get_config(config_id)

    def set_calibration(self, config_id: str, calibration_ref: str, min_detectable_tokens: int | None) -> None:
        cfg = self.get_config(config_id)
        vd = cfg["verification_defaults"]
        vd["min_detectable_tokens"] = min_detectable_tokens
        self.db.execute("UPDATE configs SET calibration_ref=?, verification_defaults=? WHERE config_id=?",
                        (calibration_ref, json.dumps(vd), config_id))
        self.db.commit()

    # keys (metadata only; secret bytes live in the backend) -------------------------------
    def add_key(self, key_id: str, purpose: str, owner_model_id: str | None, backend: str,
                backend_ref: str, key_version: int, lineage_id: str, by: str,
                state: str = "active") -> dict:
        self.db.execute(
            "INSERT INTO keys (key_id,purpose,owner_model_id,backend,backend_ref,key_version,"
            "lineage_id,state,valid_from,created_at,created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (key_id, purpose, owner_model_id, backend, backend_ref, key_version,
             lineage_id, state, _now(), _now(), by))
        self.db.commit()
        return self.get_key(key_id)

    def get_key(self, key_id: str) -> dict | None:
        r = self.db.execute("SELECT * FROM keys WHERE key_id=?", (key_id,)).fetchone()
        return dict(r) if r else None

    def list_keys(self, lineage_id: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM keys", []
        if lineage_id:
            q += " WHERE lineage_id=?"
            args.append(lineage_id)
        return [dict(r) for r in self.db.execute(q + " ORDER BY created_at, key_version", args)]

    def set_key_state(self, key_id: str, state: str, *, generate_until: str | None = None,
                      revoked_at: str | None = None, revocation_reason: str | None = None) -> dict:
        self.db.execute(
            "UPDATE keys SET state=?, generate_until=COALESCE(?, generate_until), "
            "revoked_at=COALESCE(?, revoked_at), revocation_reason=COALESCE(?, revocation_reason) "
            "WHERE key_id=?",
            (state, generate_until, revoked_at, revocation_reason, key_id))
        self.db.commit()
        return self.get_key(key_id)

    def configs_using_key(self, key_id: str, statuses: tuple[str, ...] = ("active",)) -> list[dict]:
        q = f"SELECT * FROM configs WHERE key_ref=? AND status IN ({','.join('?' * len(statuses))})"
        return [self._cfg(r) for r in self.db.execute(q, (key_id, *statuses))]

    def clone_config_for_rotation(self, old_config_id: str, new_key_id: str, by: str) -> dict:
        """Rotation (design §6 data flow 3): the old config keeps verifying
        forever; the NEW config version points at the new key and inherits the
        accepted calibration (the null distribution of the statistic under
        human text does not depend on which key is scanned). Inserted directly
        as active — the immutability trigger only guards UPDATEs."""
        old = self.get_config(old_config_id)
        if old is None:
            raise KeyError(old_config_id)
        prev = self.db.execute(
            "SELECT MAX(config_version) v FROM configs WHERE model_id=? AND model_version=?",
            (old["model_id"], old["model_version"])).fetchone()
        cfg = dict(old)
        cfg.update({
            "config_id": str(uuid.uuid4()), "config_version": (prev["v"] or 0) + 1,
            "supersedes": old_config_id, "key_ref": new_key_id, "detector_ref": None,
            "status": "active", "generate_from": _now(), "generate_until": None,
            "created_at": _now(), "created_by": by,
        })
        self.db.execute(
            "INSERT INTO configs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cfg["config_id"], cfg["model_id"], cfg["model_version"], cfg["config_version"],
             cfg["supersedes"], cfg["scheme_id"], cfg["scheme_version"],
             json.dumps(cfg["scheme_params"]), cfg["tokenizer_ref"], cfg["key_ref"], None,
             cfg["calibration_ref"], json.dumps(cfg["verification_defaults"]), "active",
             cfg["generate_from"], None, None, json.dumps(cfg["labels"]), cfg["created_at"], by))
        self.db.commit()
        return self.get_config(cfg["config_id"])

    def set_detector(self, config_id: str, detector_ref: str) -> dict:
        cfg = self.get_config(config_id)
        labels = {**cfg["labels"], "disclosure": "public-detector"}
        self.db.execute("UPDATE configs SET detector_ref=?, labels=? WHERE config_id=?",
                        (detector_ref, json.dumps(labels), config_id))
        self.db.commit()
        return self.get_config(config_id)

    # calibrations -----------------------------------------------------------------------
    def save_calibration(self, config_id: str, report: dict, by: str) -> dict:
        self.db.execute(
            "INSERT INTO calibrations VALUES (?,?,?,?,?,?,?)",
            (report["run_id"], config_id, report["created_at"], by, "ran",
             report["quality"], json.dumps(report)))
        self.db.commit()
        return self.get_calibration(report["run_id"])

    def get_calibration(self, run_id: str) -> dict | None:
        r = self.db.execute("SELECT * FROM calibrations WHERE run_id=?", (run_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["report"] = json.loads(d["report"])
        return d

    def list_calibrations(self, config_id: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT run_id, config_id, created_at, created_by, status, quality FROM calibrations "
            "WHERE config_id=? ORDER BY created_at", (config_id,))
        return [dict(r) for r in rows]

    def accept_calibration(self, run_id: str) -> dict:
        """Mark a run accepted and point its config at it. Also records the
        tokenizer hash on the config's verification defaults so the verify path
        can detect a tokenizer swap (POC plan phase 3.1)."""
        run = self.get_calibration(run_id)
        if run is None:
            raise KeyError(run_id)
        cfg = self.get_config(run["config_id"])
        if cfg is None:
            raise KeyError(run["config_id"])
        self.db.execute("UPDATE calibrations SET status='accepted' WHERE run_id=?", (run_id,))
        vd = cfg["verification_defaults"]
        vd["min_detectable_tokens"] = run["report"].get("min_detectable_tokens")
        vd["tokenizer_hash"] = run["report"].get("tokenizer_hash")
        self.db.execute("UPDATE configs SET calibration_ref=?, verification_defaults=? WHERE config_id=?",
                        (run_id, json.dumps(vd), run["config_id"]))
        self.db.commit()
        return self.get_calibration(run_id)

    def accepted_null_models(self) -> dict[str, dict]:
        """config_id -> serialized null model for every config whose
        calibration_ref points at an accepted run. Loaded at API startup."""
        rows = self.db.execute(
            "SELECT c.config_id AS config_id, cal.report AS report FROM configs c "
            "JOIN calibrations cal ON cal.run_id = c.calibration_ref "
            "WHERE cal.status='accepted'")
        return {r["config_id"]: json.loads(r["report"])["null_model"] for r in rows}

    @staticmethod
    def _cfg(r: sqlite3.Row) -> dict:
        d = dict(r)
        for k in ("scheme_params", "verification_defaults", "labels"):
            d[k] = json.loads(d[k]) if d[k] else {}
        return d

    def list_results(self, since: str | None = None, band: str | None = None,
                     model_id: str | None = None, requested_by: str | None = None,
                     limit: int = 500) -> list[dict]:
        """Result METADATA only — payloads hold statistics and digests, never text."""
        q = ("SELECT r.result_id, r.requested_at, r.requested_by, r.text_digest, r.token_count, "
             "r.fpr_target, r.band, r.best_match, c.model_id, c.model_version "
             "FROM results r LEFT JOIN configs c ON c.config_id = r.best_match WHERE 1=1")
        args: list = []
        for cond, val in ((" AND r.requested_at >= ?", since), (" AND r.band = ?", band),
                          (" AND c.model_id = ?", model_id), (" AND r.requested_by = ?", requested_by)):
            if val is not None:
                q += cond
                args.append(val)
        q += " ORDER BY r.requested_at DESC LIMIT ?"
        args.append(min(limit, 1000))
        return [dict(r) for r in self.db.execute(q, args)]

    # results (metadata only) -----------------------------------------------------------
    def save_result(self, payload: dict[str, Any]) -> None:
        assert "text" not in payload, "never store submitted text"
        self.db.execute("INSERT INTO results VALUES (?,?,?,?,?,?,?,?,?)",
                        (payload["result_id"], payload["requested_at"], payload["requested_by"], payload["text_digest"],
                         payload["token_count"], payload["fpr_target"], payload["band"], payload["best_match"],
                         json.dumps(payload)))
        self.db.commit()
