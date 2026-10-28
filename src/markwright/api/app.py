"""FastAPI application. Every surface (dashboard, CLI, SDK, inference hook) uses this.

MVP wiring: SQLite registry, local sealed vault, in-memory audit chain, bearer
token with a static role map. v1 replaces auth with OIDC and adds Postgres.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from markwright import __version__, schemes
from markwright.audit import AuditChain
from markwright.calibration import available_corpora, run_calibration
from markwright.calibration.run import tokenizer_hash as compute_tokenizer_hash
from markwright.keys.backend import BackendRef, KeySpec
from markwright.keys.local_sealed import LocalSealedVault
from markwright.registry import RegistryStore
from markwright.schemes.base import KeyHandle
from markwright.stats.null_model import NullModel
from markwright.verify import Candidate, verify

app = FastAPI(title="Markwright", version=__version__,
              description="Management layer for LLM text watermarking. Results are probabilistic evidence, never a verdict.")

# Deployment profile (design §1: one codebase, profiles selected by config).
# dev/onprem accept provisional calibrations; provider refuses them (POC 1.6).
PROFILE = os.environ.get("MARKWRIGHT_PROFILE", "dev")
# Dev default FPR is 1e-2: the bundled ~300-doc/bucket corpora resolve p down to
# ~3.3e-3 only, so a 1e-4 target would cap every band (POC 1.7). Production
# profiles keep the design default 1e-4 and must calibrate accordingly.
_DEFAULT_FPR = 1e-2 if PROFILE == "dev" else 1e-4

STATE: dict[str, Any] = {
    "registry": RegistryStore(os.environ.get("MARKWRIGHT_DB", ":memory:")),
    "vault": None,
    "audit": AuditChain(),
    "policy": {"fpr_target": float(os.environ.get("MARKWRIGHT_FPR_TARGET", _DEFAULT_FPR)),
               "retention_days": 90, "custody_default": "single"},
    "null_models": {},            # config_id -> NullModel (accepted calibrations; v1: artifact store)
    "tokenizer_hashes": {},       # tokenizer_ref -> current hash (computed once per process)
    "tokens": {"admin-token": ("admin", "admin"), "operator-token": ("op", "operator"),
               "verifier-token": ("ver", "verifier"), "auditor-token": ("aud", "auditor")},
}


def _load_null_models() -> None:
    """Hydrate accepted null models from the registry (API restart survives)."""
    for config_id, nm in STATE["registry"].accepted_null_models().items():
        STATE["null_models"][config_id] = NullModel.from_dict(nm)


_load_null_models()

ROLE_VERBS = {
    "admin": {"registry.read", "registry.write", "key.write", "verify", "audit.read", "policy.write"},
    "operator": {"registry.read", "registry.write", "key.write", "verify"},
    "verifier": {"registry.read.public", "verify"},
    "auditor": {"registry.read", "verify", "audit.read"},
}


def vault() -> LocalSealedVault:
    if STATE["vault"] is None:
        STATE["vault"] = LocalSealedVault(os.environ.get("MARKWRIGHT_VAULT", "markwright-vault.json"),
                                          os.environ.get("MARKWRIGHT_VAULT_PASSPHRASE", "dev-only-passphrase"))
    return STATE["vault"]


def principal(authorization: str = Header(default="")) -> tuple[str, str]:
    tok = authorization.removeprefix("Bearer ").strip()
    if tok not in STATE["tokens"]:
        raise HTTPException(401, "unauthenticated")
    return STATE["tokens"][tok]


def require(verb: str):
    def dep(p: tuple[str, str] = Depends(principal)):
        name, role = p
        if verb not in ROLE_VERBS[role] and not (verb == "registry.read" and "registry.read.public" in ROLE_VERBS[role]):
            STATE["audit"].append({"principal": name, "role": role}, "auth.denied", {"kind": "verb", "id": verb}, "denied")
            raise HTTPException(403, f"role {role} may not {verb}")
        return p
    return dep


# ---- schemas ---------------------------------------------------------------------------
@app.get("/v1/schemes")
def list_schemes(_=Depends(require("registry.read"))):
    out = []
    for sid, cls in schemes.available().items():
        a = cls()
        out.append({"scheme_id": sid, "scheme_version": a.scheme_version, "capabilities": a.capabilities.__dict__,
                    "params_schema": a.params_schema(), "defaults": a.default_params()})
    return out


# ---- models / configs ------------------------------------------------------------------
class ModelIn(BaseModel):
    model_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$")
    display_name: str = ""


class VersionIn(BaseModel):
    model_version: str
    tokenizer_ref: str
    model_fingerprint: str | None = None


class ConfigIn(BaseModel):
    scheme_id: str
    scheme_params: dict | None = None
    key: str = "new"              # "new" or an existing key_id
    labels: dict[str, str] = {}


@app.post("/v1/models", status_code=201)
def create_model(m: ModelIn, p=Depends(require("registry.write"))):
    reg: RegistryStore = STATE["registry"]
    if reg.get_model(m.model_id):
        raise HTTPException(409, "exists")
    out = reg.add_model(m.model_id, m.display_name, p[0])
    STATE["audit"].append({"principal": p[0], "role": p[1]}, "config.create", {"kind": "model", "id": m.model_id})
    return out


@app.get("/v1/models")
def list_models(_=Depends(require("registry.read"))):
    return STATE["registry"].list_models()


@app.post("/v1/models/{model_id}/versions", status_code=201)
def add_version(model_id: str, v: VersionIn, p=Depends(require("registry.write"))):
    if not STATE["registry"].get_model(model_id):
        raise HTTPException(404, "no such model")
    return STATE["registry"].add_version(model_id, v.model_version, v.tokenizer_ref, v.model_fingerprint)


@app.post("/v1/models/{model_id}/versions/{version}/configs", status_code=201)
def create_config(model_id: str, version: str, c: ConfigIn, p=Depends(require("registry.write"))):
    import jsonschema

    reg: RegistryStore = STATE["registry"]
    try:
        adapter = schemes.get(c.scheme_id)
    except KeyError as e:
        raise HTTPException(400, str(e)) from e
    params = {**adapter.default_params(), **(c.scheme_params or {})}
    try:
        jsonschema.validate(params, adapter.params_schema())
    except jsonschema.ValidationError as e:
        raise HTTPException(422, f"scheme_params: {e.message}") from e
    row = reg.db.execute("SELECT tokenizer_ref FROM model_versions WHERE model_id=? AND model_version=?",
                         (model_id, version)).fetchone()
    if not row:
        raise HTTPException(404, "no such model version")

    if c.key == "new":
        import uuid
        lineage = str(uuid.uuid4())
        ref = vault().create(KeySpec("generation", model_id, lineage, STATE["policy"]["custody_default"]))
        key_id = lineage
        reg.db.execute("INSERT INTO keys (key_id,purpose,owner_model_id,backend,backend_ref,key_version,lineage_id,state,valid_from,created_at,created_by) "
                       "VALUES (?,?,?,?,?,?,?,?,datetime('now'),datetime('now'),?)",
                       (key_id, "generation", model_id, ref.backend, ref.ref, ref.version, lineage, "active", p[0]))
        reg.db.commit()
        STATE["audit"].append({"principal": p[0], "role": p[1]}, "key.create", {"kind": "key", "id": key_id},
                              details={"backend": ref.backend, "custody": STATE["policy"]["custody_default"]})
    else:
        key_id = c.key

    cfg = reg.create_config(model_id, version, c.scheme_id, adapter.scheme_version, params, row["tokenizer_ref"], key_id, p[0], labels=c.labels)
    STATE["audit"].append({"principal": p[0], "role": p[1]}, "config.create", {"kind": "config", "id": cfg["config_id"]})
    return cfg


@app.get("/v1/configs/{config_id}")
def get_config(config_id: str, p=Depends(require("registry.read"))):
    cfg = STATE["registry"].get_config(config_id)
    if not cfg:
        raise HTTPException(404)
    if p[1] == "verifier":   # verifiers never see parameters or key refs
        keep = ("config_id", "model_id", "model_version", "config_version", "scheme_id", "status", "verification_defaults")
        return {k: cfg[k] for k in keep}
    return cfg


@app.post("/v1/configs/{config_id}:{action}")
def transition(config_id: str, action: str, p=Depends(require("registry.write"))):
    target = {"activate": "active", "deprecate": "deprecated", "retire": "retired"}.get(action)
    if not target:
        raise HTTPException(404)
    reg: RegistryStore = STATE["registry"]
    if target == "active":
        cfg = reg.get_config(config_id)
        if cfg and cfg["calibration_ref"]:
            run = reg.get_calibration(cfg["calibration_ref"])
            if PROFILE == "provider" and run and run["quality"] != "standard":
                raise HTTPException(409, "provider profile refuses provisional calibrations; "
                                         "recalibrate on a production-size null corpus")
    try:
        cfg = reg.transition(config_id, target)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    details = {"quality": reg.get_calibration(cfg["calibration_ref"])["quality"]} \
        if target == "active" and cfg.get("calibration_ref") else {}
    STATE["audit"].append({"principal": p[0], "role": p[1]}, f"config.{action}",
                          {"kind": "config", "id": config_id}, details=details)
    return cfg


# ---- calibration (POC: synchronous; v1 moves this to the jobs worker) --------------------
class CalibrationIn(BaseModel):
    null_corpus_ids: list[str] = Field(min_length=1)
    fpr_target: float | None = Field(default=None, gt=0, le=0.05)


@app.get("/v1/null-corpora")
def list_null_corpora(_=Depends(require("registry.read"))):
    return sorted(available_corpora())


@app.post("/v1/configs/{config_id}/calibrations", status_code=201)
def calibrate_config(config_id: str, body: CalibrationIn, p=Depends(require("registry.write"))):
    reg: RegistryStore = STATE["registry"]
    cfg = reg.get_config(config_id)
    if not cfg:
        raise HTTPException(404, "no such config")
    try:
        adapter = schemes.get(cfg["scheme_id"])
        report = run_calibration(adapter, cfg["scheme_params"], _key_handle(cfg),
                                 cfg["tokenizer_ref"], body.null_corpus_ids,
                                 body.fpr_target or STATE["policy"]["fpr_target"])
    except (KeyError, ValueError) as e:
        raise HTTPException(422, str(e)) from e
    except RuntimeError as e:   # adapter's optional deps missing (e.g. torch)
        raise HTTPException(503, str(e)) from e
    run = reg.save_calibration(config_id, report, p[0])
    STATE["audit"].append({"principal": p[0], "role": p[1]}, "calibration.run",
                          {"kind": "calibration", "id": report["run_id"]},
                          details={"config_id": config_id, "quality": report["quality"],
                                   "n_docs": report["n_docs"],
                                   "resolution_floor": report["resolution_floor"]})
    return _calibration_summary(run)


@app.get("/v1/calibrations/{run_id}")
def get_calibration(run_id: str, _=Depends(require("registry.read"))):
    run = STATE["registry"].get_calibration(run_id)
    if not run:
        raise HTTPException(404)
    return _calibration_summary(run)


@app.get("/v1/configs/{config_id}/calibrations")
def list_calibrations(config_id: str, _=Depends(require("registry.read"))):
    return STATE["registry"].list_calibrations(config_id)


@app.post("/v1/calibrations/{run_id}:accept")
def accept_calibration(run_id: str, p=Depends(require("registry.write"))):
    reg: RegistryStore = STATE["registry"]
    try:
        run = reg.accept_calibration(run_id)
    except KeyError:
        raise HTTPException(404) from None
    STATE["null_models"][run["config_id"]] = NullModel.from_dict(run["report"]["null_model"])
    STATE["audit"].append({"principal": p[0], "role": p[1]}, "calibration.accept",
                          {"kind": "calibration", "id": run_id},
                          details={"config_id": run["config_id"], "quality": run["quality"]})
    return _calibration_summary(run)


def _calibration_summary(run: dict) -> dict:
    """Everything except the (large) serialized null model."""
    r = run["report"]
    return {"run_id": run["run_id"], "config_id": run["config_id"], "status": run["status"],
            "created_at": run["created_at"], "created_by": run["created_by"],
            **{k: r[k] for k in ("null_corpus_ids", "n_docs", "quality", "resolution_floor",
                                 "buckets", "fpr_target", "tokenizer_ref", "tokenizer_hash",
                                 "min_detectable_tokens", "notes")}}


# ---- generation profile (design §8.1; consumed by the inference hook) --------------------
@app.get("/v1/models/{model_id}/versions/{version}/generation-profile")
def generation_profile(model_id: str, version: str, config_id: str | None = None,
                       p=Depends(require("key.write"))):
    """Scheme id, params and derived key material for the inference hook.

    MVP form: material is returned sealed-to-nobody (hex) over the local API —
    acceptable for the dev/laptop loop only. v1 requires an X-Session-PubKey
    (X25519) and seals to it with a TTL (design §6.2); the response shape is
    already final so hooks won't change."""
    reg: RegistryStore = STATE["registry"]
    cfgs = [reg.get_config(config_id)] if config_id else \
        [c for c in reg.list_configs(model_id=model_id, statuses=("active",)) if c["model_version"] == version]
    cfg = next((c for c in cfgs if c and c["model_id"] == model_id and c["model_version"] == version), None)
    if cfg is None:
        raise HTTPException(404, "no matching config (pass ?config_id= for a draft config)")
    if cfg["status"] in ("deprecated", "retired"):
        raise HTTPException(409, f"config is {cfg['status']}; it must not mark new text")
    key_row = reg.db.execute("SELECT * FROM keys WHERE key_id=?", (cfg["key_ref"],)).fetchone()
    handle = _key_handle(cfg)
    STATE["audit"].append({"principal": p[0], "role": p[1]}, "key.handle_issue",
                          {"kind": "config", "id": cfg["config_id"]},
                          details={"model_id": model_id, "model_version": version,
                                   "key_version": key_row["key_version"]})
    return {
        "config_id": cfg["config_id"],
        "scheme_id": cfg["scheme_id"],
        "scheme_version": cfg["scheme_version"],
        "scheme_params": cfg["scheme_params"],
        "tokenizer_ref": cfg["tokenizer_ref"],
        "key_ref": cfg["key_ref"],
        "key_version": key_row["key_version"],
        "backend": key_row["backend"],
        "backend_ref": key_row["backend_ref"],
        "sealed_material": handle.sealed_material.hex(),
        "status": cfg["status"],
    }


# ---- verification ----------------------------------------------------------------------
class VerifyIn(BaseModel):
    text: str | None = None
    token_ids: list[int] | None = None     # design §8.1: body is {text} or {token_ids}
    config_ids: list[str] | None = None
    fpr_target: float | None = Field(default=None, gt=0, le=0.05, description="may only tighten the policy value")


def _count_tokens(text: str, tokenizer_ref: str) -> int:
    """Exact token count or an exception. NEVER a whitespace fallback: a wrong
    count silently mis-gates and a wrong tokenizer produces confident nonsense
    (design §5 / §7.1 step 1). The pipeline maps the exception to
    `tokenizer_uncertain` + insufficient_evidence."""
    from transformers import AutoTokenizer
    return len(AutoTokenizer.from_pretrained(tokenizer_ref)(text, add_special_tokens=False)["input_ids"])


def _current_tokenizer_hash(tokenizer_ref: str) -> str | None:
    if tokenizer_ref not in STATE["tokenizer_hashes"]:
        STATE["tokenizer_hashes"][tokenizer_ref] = compute_tokenizer_hash(tokenizer_ref)
    return STATE["tokenizer_hashes"][tokenizer_ref]


def _key_handle(cfg: dict) -> KeyHandle:
    """Derive this config's scheme material through the key backend."""
    key_row = STATE["registry"].db.execute(
        "SELECT * FROM keys WHERE key_id=?", (cfg["key_ref"],)).fetchone()
    if key_row is None:
        raise HTTPException(409, f"config {cfg['config_id']} references unknown key {cfg['key_ref']}")
    material = vault().derive(
        BackendRef(key_row["backend"], key_row["backend_ref"], key_row["key_version"]),
        f"markwright/root/{cfg['key_ref']}", 32)
    return KeyHandle(cfg["key_ref"], key_row["key_version"], key_row["backend"],
                     key_row["backend_ref"], material)


@app.post("/v1/verify")
def verify_text(v: VerifyIn, p=Depends(require("verify"))):
    if (v.text is None) == (v.token_ids is None):
        raise HTTPException(422, "provide exactly one of text or token_ids")
    reg: RegistryStore = STATE["registry"]
    fpr = min(STATE["policy"]["fpr_target"], v.fpr_target or 1.0)
    cfgs = [reg.get_config(c) for c in v.config_ids] if v.config_ids else reg.list_configs(statuses=("active", "deprecated"))
    cands: list[Candidate] = []
    for cfg in cfgs:
        if not cfg or cfg["config_id"] not in STATE["null_models"]:
            continue   # uncalibrated configs are never scanned
        flags = ["historical_key"] if cfg["status"] == "deprecated" else []
        if cfg["labels"].get("disclosure") == "public-detector":
            flags.append("spoofing_not_excluded")
        # Tokenizer-swap detection (POC 3.1): the hash recorded at calibration
        # must match the current one. Only text input touches a tokenizer;
        # token-ids submissions bypass it by construction.
        if v.text is not None:
            recorded = cfg["verification_defaults"].get("tokenizer_hash")
            if recorded is not None and _current_tokenizer_hash(cfg["tokenizer_ref"]) != recorded:
                flags.append("tokenizer_uncertain")
        key_row = reg.db.execute("SELECT * FROM keys WHERE key_id=?", (cfg["key_ref"],)).fetchone()
        cands.append(Candidate(cfg["config_id"], cfg["scheme_id"], schemes.get(cfg["scheme_id"]), cfg["scheme_params"],
                               _key_handle(cfg), cfg["tokenizer_ref"], STATE["null_models"][cfg["config_id"]],
                               cfg["verification_defaults"]["min_tokens"], cfg["verification_defaults"].get("min_detectable_tokens"),
                               key_row["key_version"], flags))
    salt = os.environ.get("MARKWRIGHT_DIGEST_SALT", "dev-salt").encode()
    res = verify(v.text if v.text is not None else v.token_ids, cands, fpr,
                 count_tokens=_count_tokens, deployment_salt=salt, requested_by=p[0])
    reg.save_result(res.payload)
    STATE["audit"].append({"principal": p[0], "role": p[1]}, "verify.single", {"kind": "result", "id": res["result_id"]},
                          details={"band": res["band"], "token_count": res["token_count"], "candidates": len(cands)})
    return res.payload


# ---- audit / policy / health -----------------------------------------------------------
@app.get("/v1/audit")
def audit(_=Depends(require("audit.read"))):
    return {"head": STATE["audit"].head, "entries": STATE["audit"].entries}


@app.get("/v1/policy")
def get_policy(_=Depends(require("registry.read"))):
    return STATE["policy"]


@app.get("/healthz")
def healthz():
    return {"ok": True, "version": __version__}
