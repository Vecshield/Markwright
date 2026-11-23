"""Registry store on SQLAlchemy Core: SQLite (single-node/offline) and
PostgreSQL (production) behind the same methods (design §9, POC 8.4).

``RegistryStore`` accepts a file path / ``:memory:`` (SQLite, back-compat) or
any SQLAlchemy URL (``postgresql+psycopg://...``). All SQL is dialect-portable
named-parameter text; the one dialect-specific piece is the config-immutability
trigger (design §4: immutability is a DB constraint, not a convention), which
raises :class:`ImmutableConfigError` uniformly on both engines.

Schema management is ``CREATE IF NOT EXISTS`` for the POC — fresh deployments
only; Alembic migrations are the v1 follow-up recorded in the plan.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import StaticPool

SCHEMA_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS models (
      model_id TEXT PRIMARY KEY, display_name TEXT, created_at TEXT, created_by TEXT)""",
    """CREATE TABLE IF NOT EXISTS model_versions (
      model_id TEXT, model_version TEXT, tokenizer_ref TEXT, model_fingerprint TEXT, created_at TEXT,
      PRIMARY KEY (model_id, model_version))""",
    """CREATE TABLE IF NOT EXISTS configs (
      config_id TEXT PRIMARY KEY, model_id TEXT, model_version TEXT, config_version INTEGER,
      supersedes TEXT, scheme_id TEXT, scheme_version TEXT, scheme_params TEXT, tokenizer_ref TEXT,
      key_ref TEXT, detector_ref TEXT, calibration_ref TEXT, verification_defaults TEXT,
      status TEXT, generate_from TEXT, generate_until TEXT, verify_until TEXT, labels TEXT,
      created_at TEXT, created_by TEXT)""",
    """CREATE TABLE IF NOT EXISTS keys (
      key_id TEXT PRIMARY KEY, purpose TEXT, owner_model_id TEXT, backend TEXT, backend_ref TEXT,
      key_version INTEGER, lineage_id TEXT, derivation TEXT, custody TEXT, state TEXT,
      valid_from TEXT, generate_until TEXT, verify_until TEXT, rotation_policy TEXT,
      public_detector_ref TEXT, created_at TEXT, created_by TEXT, revoked_at TEXT, revocation_reason TEXT)""",
    """CREATE TABLE IF NOT EXISTS results (
      result_id TEXT PRIMARY KEY, requested_at TEXT, requested_by TEXT, text_digest TEXT,
      token_count INTEGER, fpr_target REAL, band TEXT, best_match TEXT, payload TEXT)""",
    """CREATE TABLE IF NOT EXISTS calibrations (
      run_id TEXT PRIMARY KEY, config_id TEXT, created_at TEXT, created_by TEXT,
      status TEXT, quality TEXT, report TEXT)""",
]

IMMUTABLE_MSG = "config is immutable once active (create a new config version instead)"

# Identity-defining columns may not change once a config leaves draft. Lifecycle
# columns (status, dates), calibration_ref/verification_defaults, detector_ref
# and labels stay mutable; rotation creates NEW rows instead of updating.
TRIGGER_SQLITE = f"""
CREATE TRIGGER IF NOT EXISTS configs_immutable
BEFORE UPDATE OF model_id, model_version, config_version, supersedes,
                 scheme_id, scheme_version, scheme_params, tokenizer_ref, key_ref
ON configs WHEN old.status != 'draft'
BEGIN
  SELECT RAISE(ABORT, '{IMMUTABLE_MSG}');
END"""

TRIGGER_PG_FN = f"""
CREATE OR REPLACE FUNCTION configs_immutable_fn() RETURNS trigger AS $$
BEGIN
  IF OLD.status != 'draft' AND (
       NEW.model_id       IS DISTINCT FROM OLD.model_id OR
       NEW.model_version  IS DISTINCT FROM OLD.model_version OR
       NEW.config_version IS DISTINCT FROM OLD.config_version OR
       NEW.supersedes     IS DISTINCT FROM OLD.supersedes OR
       NEW.scheme_id      IS DISTINCT FROM OLD.scheme_id OR
       NEW.scheme_version IS DISTINCT FROM OLD.scheme_version OR
       NEW.scheme_params  IS DISTINCT FROM OLD.scheme_params OR
       NEW.tokenizer_ref  IS DISTINCT FROM OLD.tokenizer_ref OR
       NEW.key_ref        IS DISTINCT FROM OLD.key_ref) THEN
    RAISE EXCEPTION '{IMMUTABLE_MSG}';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql"""

TRIGGER_PG = """
DROP TRIGGER IF EXISTS configs_immutable ON configs;
CREATE TRIGGER configs_immutable BEFORE UPDATE ON configs
FOR EACH ROW EXECUTE FUNCTION configs_immutable_fn()"""

TRANSITIONS = {"draft": {"active", "retired"}, "active": {"deprecated"}, "deprecated": {"retired"}, "retired": set()}


class ImmutableConfigError(Exception):
    """Raised uniformly on both dialects when the DB trigger fires."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    """Thin portable layer: named-parameter SQL in, list-of-dicts out, one
    transaction per statement (matching the previous autocommit behaviour)."""

    def __init__(self, url_or_path: str | Path):
        s = str(url_or_path)
        if "://" not in s:
            url = "sqlite://" if s == ":memory:" else f"sqlite:///{s}"
        else:
            url = s
        kwargs: dict[str, Any] = {}
        if url.startswith("sqlite"):
            # one shared connection so :memory: and threads behave like before
            kwargs = {"connect_args": {"check_same_thread": False}, "poolclass": StaticPool}
        self.engine = create_engine(url, **kwargs)
        self.dialect = self.engine.dialect.name

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        try:
            with self.engine.begin() as cx:
                res = cx.execute(text(sql), params or {})
                return [dict(r) for r in res.mappings()] if res.returns_rows else []
        except DBAPIError as e:
            if IMMUTABLE_MSG.split(" (")[0] in str(e.orig):
                raise ImmutableConfigError(IMMUTABLE_MSG) from e
            raise

    def one(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        rows = self.execute(sql, params)
        return rows[0] if rows else None


class RegistryStore:
    def __init__(self, url_or_path: str | Path = ":memory:"):
        self.db = Database(url_or_path)
        for stmt in SCHEMA_STATEMENTS:
            self.db.execute(stmt)
        if self.db.dialect == "sqlite":
            self.db.execute(TRIGGER_SQLITE)
        else:
            self.db.execute(TRIGGER_PG_FN)
            for stmt in TRIGGER_PG.strip().split(";\n"):
                self.db.execute(stmt)

    # models ---------------------------------------------------------------------------
    def add_model(self, model_id: str, display_name: str, by: str) -> dict:
        self.db.execute("INSERT INTO models (model_id, display_name, created_at, created_by) "
                        "VALUES (:id, :name, :now, :by)",
                        {"id": model_id, "name": display_name, "now": _now(), "by": by})
        return self.get_model(model_id)

    def get_model(self, model_id: str) -> dict | None:
        return self.db.one("SELECT * FROM models WHERE model_id=:id", {"id": model_id})

    def list_models(self) -> list[dict]:
        return self.db.execute("SELECT * FROM models ORDER BY created_at")

    def add_version(self, model_id: str, version: str, tokenizer_ref: str, fingerprint: str | None = None) -> dict:
        self.db.execute(
            "INSERT INTO model_versions (model_id, model_version, tokenizer_ref, model_fingerprint, created_at) "
            "VALUES (:id, :v, :tok, :fp, :now)",
            {"id": model_id, "v": version, "tok": tokenizer_ref, "fp": fingerprint, "now": _now()})
        return {"model_id": model_id, "model_version": version, "tokenizer_ref": tokenizer_ref}

    def get_model_version(self, model_id: str, version: str) -> dict | None:
        return self.db.one("SELECT * FROM model_versions WHERE model_id=:id AND model_version=:v",
                           {"id": model_id, "v": version})

    # configs --------------------------------------------------------------------------
    _CONFIG_COLS = ("config_id, model_id, model_version, config_version, supersedes, scheme_id, "
                    "scheme_version, scheme_params, tokenizer_ref, key_ref, detector_ref, "
                    "calibration_ref, verification_defaults, status, generate_from, generate_until, "
                    "verify_until, labels, created_at, created_by")

    def _insert_config(self, cfg: dict) -> None:
        row = dict(cfg)
        for k in ("scheme_params", "verification_defaults", "labels"):
            row[k] = json.dumps(row[k])
        placeholders = ", ".join(f":{c.strip()}" for c in self._CONFIG_COLS.split(","))
        self.db.execute(f"INSERT INTO configs ({self._CONFIG_COLS}) VALUES ({placeholders})", row)

    def create_config(self, model_id: str, model_version: str, scheme_id: str, scheme_version: str,
                      scheme_params: dict, tokenizer_ref: str, key_ref: str, by: str,
                      verification_defaults: dict | None = None, labels: dict | None = None) -> dict:
        prev = self.db.one(
            "SELECT MAX(config_version) AS v FROM configs WHERE model_id=:id AND model_version=:mv",
            {"id": model_id, "mv": model_version})
        cfg = {
            "config_id": str(uuid.uuid4()), "model_id": model_id, "model_version": model_version,
            "config_version": (prev["v"] or 0) + 1, "supersedes": None,
            "scheme_id": scheme_id, "scheme_version": scheme_version, "scheme_params": scheme_params,
            "tokenizer_ref": tokenizer_ref, "key_ref": key_ref, "detector_ref": None, "calibration_ref": None,
            "verification_defaults": verification_defaults or {"fpr_target": 1e-4, "min_tokens": 50, "min_detectable_tokens": None},
            "status": "draft", "generate_from": None, "generate_until": None, "verify_until": None,
            "labels": labels or {}, "created_at": _now(), "created_by": by,
        }
        if cfg["config_version"] > 1:
            latest = self.db.one(
                "SELECT config_id FROM configs WHERE model_id=:id AND model_version=:mv "
                "AND config_version=:cv",
                {"id": model_id, "mv": model_version, "cv": cfg["config_version"] - 1})
            cfg["supersedes"] = latest["config_id"] if latest else None
        self._insert_config(cfg)
        return cfg

    def get_config(self, config_id: str) -> dict | None:
        r = self.db.one("SELECT * FROM configs WHERE config_id=:id", {"id": config_id})
        return self._cfg(r) if r else None

    def list_configs(self, model_id: str | None = None, statuses: tuple[str, ...] | None = None) -> list[dict]:
        q, params = "SELECT * FROM configs WHERE 1=1", {}
        if model_id:
            q += " AND model_id=:mid"
            params["mid"] = model_id
        if statuses:
            names = [f"s{i}" for i in range(len(statuses))]
            q += f" AND status IN ({', '.join(':' + n for n in names)})"
            params.update(dict(zip(names, statuses, strict=True)))
        return [self._cfg(r) for r in self.db.execute(q + " ORDER BY created_at", params)]

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
        sets = ", ".join(f"{k}=:{k}" for k in cols)
        self.db.execute(f"UPDATE configs SET {sets} WHERE config_id=:id", {**cols, "id": config_id})
        return self.get_config(config_id)

    def set_calibration(self, config_id: str, calibration_ref: str, min_detectable_tokens: int | None) -> None:
        cfg = self.get_config(config_id)
        vd = cfg["verification_defaults"]
        vd["min_detectable_tokens"] = min_detectable_tokens
        self.db.execute("UPDATE configs SET calibration_ref=:ref, verification_defaults=:vd WHERE config_id=:id",
                        {"ref": calibration_ref, "vd": json.dumps(vd), "id": config_id})

    # keys (metadata only; secret bytes live in the backend) -------------------------------
    def add_key(self, key_id: str, purpose: str, owner_model_id: str | None, backend: str,
                backend_ref: str, key_version: int, lineage_id: str, by: str,
                state: str = "active") -> dict:
        self.db.execute(
            "INSERT INTO keys (key_id, purpose, owner_model_id, backend, backend_ref, key_version, "
            "lineage_id, state, valid_from, created_at, created_by) "
            "VALUES (:kid, :p, :own, :b, :bref, :kv, :lin, :st, :now, :now, :by)",
            {"kid": key_id, "p": purpose, "own": owner_model_id, "b": backend, "bref": backend_ref,
             "kv": key_version, "lin": lineage_id, "st": state, "now": _now(), "by": by})
        return self.get_key(key_id)

    def get_key(self, key_id: str) -> dict | None:
        return self.db.one("SELECT * FROM keys WHERE key_id=:id", {"id": key_id})

    def get_signing_key(self) -> dict | None:
        return self.db.one("SELECT * FROM keys WHERE purpose='signing' AND state='active'")

    def list_keys(self, lineage_id: str | None = None) -> list[dict]:
        if lineage_id:
            return self.db.execute("SELECT * FROM keys WHERE lineage_id=:lin ORDER BY created_at, key_version",
                                   {"lin": lineage_id})
        return self.db.execute("SELECT * FROM keys ORDER BY created_at, key_version")

    def set_key_state(self, key_id: str, state: str, *, generate_until: str | None = None,
                      revoked_at: str | None = None, revocation_reason: str | None = None) -> dict:
        self.db.execute(
            "UPDATE keys SET state=:st, generate_until=COALESCE(:gu, generate_until), "
            "revoked_at=COALESCE(:ra, revoked_at), revocation_reason=COALESCE(:rr, revocation_reason) "
            "WHERE key_id=:id",
            {"st": state, "gu": generate_until, "ra": revoked_at, "rr": revocation_reason, "id": key_id})
        return self.get_key(key_id)

    def configs_using_key(self, key_id: str, statuses: tuple[str, ...] = ("active",)) -> list[dict]:
        names = [f"s{i}" for i in range(len(statuses))]
        rows = self.db.execute(
            f"SELECT * FROM configs WHERE key_ref=:kid AND status IN ({', '.join(':' + n for n in names)})",
            {"kid": key_id, **dict(zip(names, statuses, strict=True))})
        return [self._cfg(r) for r in rows]

    def clone_config_for_rotation(self, old_config_id: str, new_key_id: str, by: str) -> dict:
        """Rotation (design §6 data flow 3): the old config keeps verifying
        forever; the NEW config version points at the new key and inherits the
        accepted calibration (the null distribution of the statistic under
        human text does not depend on which key is scanned). Inserted directly
        as active — the immutability trigger only guards UPDATEs."""
        old = self.get_config(old_config_id)
        if old is None:
            raise KeyError(old_config_id)
        prev = self.db.one(
            "SELECT MAX(config_version) AS v FROM configs WHERE model_id=:id AND model_version=:mv",
            {"id": old["model_id"], "mv": old["model_version"]})
        cfg = dict(old)
        labels = {k: v for k, v in old.get("labels", {}).items() if k != "disclosure"}
        cfg.update({
            "config_id": str(uuid.uuid4()), "config_version": (prev["v"] or 0) + 1,
            "supersedes": old_config_id, "key_ref": new_key_id, "detector_ref": None,
            "status": "active", "generate_from": _now(), "generate_until": None,
            "labels": labels,
            "created_at": _now(), "created_by": by,
        })
        self._insert_config(cfg)
        return self.get_config(cfg["config_id"])

    def set_detector(self, config_id: str, detector_ref: str) -> dict:
        cfg = self.get_config(config_id)
        if cfg is None:
            raise KeyError(config_id)
        labels = {**cfg["labels"], "disclosure": "public-detector"}
        self.db.execute("UPDATE configs SET detector_ref=:ref, labels=:lab WHERE config_id=:id",
                        {"ref": detector_ref, "lab": json.dumps(labels), "id": config_id})
        return self.get_config(config_id)

    # calibrations -----------------------------------------------------------------------
    def save_calibration(self, config_id: str, report: dict, by: str) -> dict:
        self.db.execute(
            "INSERT INTO calibrations (run_id, config_id, created_at, created_by, status, quality, report) "
            "VALUES (:rid, :cid, :at, :by, 'ran', :q, :rep)",
            {"rid": report["run_id"], "cid": config_id, "at": report["created_at"], "by": by,
             "q": report["quality"], "rep": json.dumps(report)})
        return self.get_calibration(report["run_id"])

    def get_calibration(self, run_id: str) -> dict | None:
        d = self.db.one("SELECT * FROM calibrations WHERE run_id=:id", {"id": run_id})
        if not d:
            return None
        d["report"] = json.loads(d["report"])
        return d

    def list_calibrations(self, config_id: str) -> list[dict]:
        return self.db.execute(
            "SELECT run_id, config_id, created_at, created_by, status, quality FROM calibrations "
            "WHERE config_id=:id ORDER BY created_at", {"id": config_id})

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
        self.db.execute("UPDATE calibrations SET status='accepted' WHERE run_id=:id", {"id": run_id})
        vd = cfg["verification_defaults"]
        vd["min_detectable_tokens"] = run["report"].get("min_detectable_tokens")
        vd["tokenizer_hash"] = run["report"].get("tokenizer_hash")
        self.db.execute("UPDATE configs SET calibration_ref=:ref, verification_defaults=:vd WHERE config_id=:id",
                        {"ref": run_id, "vd": json.dumps(vd), "id": run["config_id"]})
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
    def _cfg(r: dict) -> dict:
        d = dict(r)
        for k in ("scheme_params", "verification_defaults", "labels"):
            d[k] = json.loads(d[k]) if d[k] else {}
        return d

    # results (metadata only) -----------------------------------------------------------
    def save_result(self, payload: dict[str, Any]) -> None:
        assert "text" not in payload, "never store submitted text"
        self.db.execute(
            "INSERT INTO results (result_id, requested_at, requested_by, text_digest, token_count, "
            "fpr_target, band, best_match, payload) "
            "VALUES (:rid, :at, :by, :dig, :tc, :fpr, :band, :best, :pl)",
            {"rid": payload["result_id"], "at": payload["requested_at"], "by": payload["requested_by"],
             "dig": payload["text_digest"], "tc": payload["token_count"], "fpr": payload["fpr_target"],
             "band": payload["band"], "best": payload["best_match"], "pl": json.dumps(payload)})

    def list_results(self, since: str | None = None, band: str | None = None,
                     model_id: str | None = None, requested_by: str | None = None,
                     limit: int = 500) -> list[dict]:
        """Result METADATA only — payloads hold statistics and digests, never text."""
        q = ("SELECT r.result_id, r.requested_at, r.requested_by, r.text_digest, r.token_count, "
             "r.fpr_target, r.band, r.best_match, c.model_id, c.model_version "
             "FROM results r LEFT JOIN configs c ON c.config_id = r.best_match WHERE 1=1")
        params: dict[str, Any] = {}
        for cond, name, val in ((" AND r.requested_at >= :since", "since", since),
                                (" AND r.band = :band", "band", band),
                                (" AND c.model_id = :mid", "mid", model_id),
                                (" AND r.requested_by = :rby", "rby", requested_by)):
            if val is not None:
                q += cond
                params[name] = val
        q += " ORDER BY r.requested_at DESC LIMIT :lim"
        params["lim"] = min(limit, 1000)
        return self.db.execute(q, params)

    def result_counts_by_band(self) -> list[dict]:
        return self.db.execute("SELECT band, COUNT(*) AS n FROM results GROUP BY band")

    def config_counts_by_status(self) -> list[dict]:
        return self.db.execute("SELECT status, COUNT(*) AS n FROM configs GROUP BY status")
