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
from markwright.keys.local_sealed import LocalSealedVault
from markwright.keys.backend import KeySpec
from markwright.registry import RegistryStore
from markwright.schemes.base import KeyHandle
from markwright.stats.null_model import NullModel
from markwright.verify import Candidate, verify

app = FastAPI(title="Markwright", version=__version__,
              description="Management layer for LLM text watermarking. Results are probabilistic evidence, never a verdict.")

STATE: dict[str, Any] = {
    "registry": RegistryStore(os.environ.get("MARKWRIGHT_DB", ":memory:")),
    "vault": None,
    "audit": AuditChain(),
    "policy": {"fpr_target": 1e-4, "retention_days": 90, "custody_default": "single"},
    "null_models": {},            # config_id -> NullModel (MVP: in memory; v1: artifact store)
    "tokens": {"admin-token": ("admin", "admin"), "operator-token": ("op", "operator"),
               "verifier-token": ("ver", "verifier"), "auditor-token": ("aud", "auditor")},
}

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
        raise HTTPException(400, str(e))
    params = {**adapter.default_params(), **(c.scheme_params or {})}
    try:
        jsonschema.validate(params, adapter.params_schema())
    except jsonschema.ValidationError as e:
        raise HTTPException(422, f"scheme_params: {e.message}")
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
    try:
        cfg = STATE["registry"].transition(config_id, target)
    except ValueError as e:
        raise HTTPException(409, str(e))
    STATE["audit"].append({"principal": p[0], "role": p[1]}, f"config.{action}", {"kind": "config", "id": config_id})
    return cfg


# ---- verification ----------------------------------------------------------------------
class VerifyIn(BaseModel):
    text: str
    config_ids: list[str] | None = None
    fpr_target: float | None = Field(default=None, gt=0, le=0.05, description="may only tighten the policy value")


def _count_tokens(text: str, tokenizer_ref: str) -> int:
    try:
        from transformers import AutoTokenizer
        return len(AutoTokenizer.from_pretrained(tokenizer_ref)(text, add_special_tokens=False)["input_ids"])
    except Exception:
        return len(text.split())   # dev fallback; a real deployment requires the tokenizer


@app.post("/v1/verify")
def verify_text(v: VerifyIn, p=Depends(require("verify"))):
    reg: RegistryStore = STATE["registry"]
    fpr = min(STATE["policy"]["fpr_target"], v.fpr_target or 1.0)
    cfgs = [reg.get_config(c) for c in v.config_ids] if v.config_ids else reg.list_configs(statuses=("active", "deprecated"))
    cands: list[Candidate] = []
    for cfg in cfgs:
        if not cfg or cfg["config_id"] not in STATE["null_models"]:
            continue   # uncalibrated configs are never scanned
        key_row = reg.db.execute("SELECT * FROM keys WHERE key_id=?", (cfg["key_ref"],)).fetchone()
        material = vault().derive(__import__("markwright.keys.backend", fromlist=["BackendRef"]).BackendRef(
            key_row["backend"], key_row["backend_ref"], key_row["key_version"]), f"markwright/root/{cfg['key_ref']}", 32)
        handle = KeyHandle(cfg["key_ref"], key_row["key_version"], key_row["backend"], key_row["backend_ref"], material)
        flags = ["historical_key"] if cfg["status"] == "deprecated" else []
        if cfg["labels"].get("disclosure") == "public-detector":
            flags.append("spoofing_not_excluded")
        cands.append(Candidate(cfg["config_id"], cfg["scheme_id"], schemes.get(cfg["scheme_id"]), cfg["scheme_params"],
                               handle, cfg["tokenizer_ref"], STATE["null_models"][cfg["config_id"]],
                               cfg["verification_defaults"]["min_tokens"], cfg["verification_defaults"].get("min_detectable_tokens"),
                               key_row["key_version"], flags))
    res = verify(v.text, cands, fpr, count_tokens=_count_tokens, deployment_salt=b"dev-salt", requested_by=p[0])
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
