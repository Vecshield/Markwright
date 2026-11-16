"""FastAPI application. Every surface (dashboard, CLI, SDK, inference hook) uses this.

MVP wiring: SQLite registry, local sealed vault, in-memory audit chain, bearer
token with a static role map. v1 replaces auth with OIDC and adds Postgres.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from markwright import __version__, schemes
from markwright.artifacts import LocalArtifactStore
from markwright.audit import AuditChain, export_bundle
from markwright.calibration import available_corpora, run_calibration
from markwright.calibration.run import tokenizer_hash as compute_tokenizer_hash
from markwright.keys.backend import BackendRef, KeySpec
from markwright.keys.local_sealed import LocalSealedVault
from markwright.registry import RegistryStore
from markwright.schemes.base import KeyHandle
from markwright.stats.null_model import NullModel
from markwright.verify import Candidate, verify

app = FastAPI(title="Markwright", version=__version__,
              description="Control plane for LLM text watermarking. Results are calibrated statistical evidence with an explicit false-positive target; the API does not decide whether text is AI-written.")

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
    "audit": AuditChain(),        # replaced by the persistent signed chain in _init_audit()
    "artifacts": LocalArtifactStore(os.environ.get("MARKWRIGHT_ARTIFACTS", "artifacts")),
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


ROLE_VERBS = {
    "admin": {"registry.read", "registry.write", "key.read", "key.write", "key.destroy",
              "verify", "audit.read", "policy.write"},
    "operator": {"registry.read", "registry.write", "key.read", "key.write", "verify"},
    "verifier": {"registry.read.public", "verify"},
    "auditor": {"registry.read", "key.read", "verify", "audit.read"},
}


def _make_backend(name: str):
    if name == "vault-transit":
        from markwright.keys.vault_transit import VaultTransitBackend
        return VaultTransitBackend()
    if name == "local-sealed":
        return LocalSealedVault(os.environ.get("MARKWRIGHT_VAULT", "markwright-vault.json"),
                                os.environ.get("MARKWRIGHT_VAULT_PASSPHRASE", "dev-only-passphrase"))
    raise RuntimeError(f"unknown key backend '{name}'")


def vault():
    """The deployment's DEFAULT KeyBackend — where NEW keys are created. Chosen
    by configuration, not code (design §3): MARKWRIGHT_KEY_BACKEND =
    local-sealed (default; air-gapped/dev) | vault-transit."""
    if STATE["vault"] is None:
        STATE["vault"] = _make_backend(os.environ.get("MARKWRIGHT_KEY_BACKEND", "local-sealed"))
    return STATE["vault"]


def backend_for(name: str):
    """Resolve a key RECORD's backend. Every key row names the backend that
    holds its bytes; derivation must go to that backend, not whatever the
    deployment currently defaults to — otherwise a backend migration would
    silently derive garbage from the wrong store."""
    v = vault()
    if v.name == name:
        return v
    cache = STATE.setdefault("backends", {})
    if name not in cache:
        cache[name] = _make_backend(name)
    return cache[name]


def _ensure_signing_key():
    """Deployment Ed25519 signing identity (design §6: generation secrets and
    signing identities are separate key records with separate purposes). The
    seed is derived inside the vault boundary; only the derivation is repeated,
    never stored."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    reg: RegistryStore = STATE["registry"]
    row = reg.get_signing_key()
    if row is None:
        import uuid
        lineage = str(uuid.uuid4())
        ref = vault().create(KeySpec("signing", None, lineage))
        row_d = reg.add_key(lineage, "signing", None, ref.backend, ref.ref, ref.version, lineage, "system")
    else:
        row_d = row
    seed = backend_for(row_d["backend"]).derive(BackendRef(row_d["backend"], row_d["backend_ref"], row_d["key_version"]),
                          "markwright/deployment-signing/v1", 32)
    return Ed25519PrivateKey.from_private_bytes(seed)


def _init_audit() -> None:
    """Persistent, signed audit chain (POC 4.3): entries live in the registry
    DB and survive restarts; anchors are signed with the deployment identity."""
    STATE["audit"] = AuditChain(
        _ensure_signing_key(),
        anchor_every=int(os.environ.get("MARKWRIGHT_ANCHOR_EVERY", "100")),
        db=STATE["registry"].db)


def _init_oidc() -> None:
    """OIDC bearer auth (design §6.3) when an issuer is configured; the static
    dev tokens then stop working unless break-glass is explicitly enabled
    (never in the provider profile), and each break-glass auth is audited."""
    issuer = os.environ.get("MARKWRIGHT_OIDC_ISSUER")
    if not issuer:
        STATE["oidc"] = None
        return
    from markwright.api.auth import OIDCValidator
    STATE["oidc"] = OIDCValidator(
        issuer,
        audience=os.environ.get("MARKWRIGHT_OIDC_AUDIENCE", "markwright"),
        role_claim=os.environ.get("MARKWRIGHT_OIDC_ROLE_CLAIM", "roles"),
    )


_load_null_models()
_init_audit()
_init_oidc()


def principal(authorization: str = Header(default="")) -> tuple[str, str]:
    tok = authorization.removeprefix("Bearer ").strip()
    if STATE.get("oidc") is not None:
        from markwright.api.auth import OIDCError
        try:
            return STATE["oidc"].principal(tok)
        except OIDCError as e:
            # break-glass: explicit, local, logged, unavailable in provider (design §6.3)
            if (tok in STATE["tokens"]
                    and os.environ.get("MARKWRIGHT_BREAK_GLASS") == "1"
                    and PROFILE != "provider"):
                name, role = STATE["tokens"][tok]
                STATE["audit"].append({"principal": name, "role": role}, "auth.login",
                                      {"kind": "principal", "id": name},
                                      details={"method": "break-glass-static-token"})
                return name, role
            raise HTTPException(401, str(e)) from e
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
    row = reg.get_model_version(model_id, version)
    if not row:
        raise HTTPException(404, "no such model version")

    if c.key == "new":
        import uuid
        lineage = str(uuid.uuid4())
        ref = vault().create(KeySpec("generation", model_id, lineage, STATE["policy"]["custody_default"]))
        key_id = lineage
        reg.add_key(key_id, "generation", model_id, ref.backend, ref.ref, ref.version, lineage, p[0])
        STATE["audit"].append({"principal": p[0], "role": p[1]}, "key.create", {"kind": "key", "id": key_id},
                              details={"backend": ref.backend, "custody": STATE["policy"]["custody_default"]})
    else:
        key_id = c.key
        krow = reg.get_key(key_id)
        if krow is None or krow["purpose"] != "generation":
            raise HTTPException(422, "key must exist and have purpose=generation")

    cfg = reg.create_config(model_id, version, c.scheme_id, adapter.scheme_version, params, row["tokenizer_ref"], key_id, p[0], labels=c.labels)
    STATE["audit"].append({"principal": p[0], "role": p[1]}, "config.create", {"kind": "config", "id": cfg["config_id"]})
    return cfg


_VERIFIER_CONFIG_FIELDS = ("config_id", "model_id", "model_version", "config_version",
                           "scheme_id", "status", "verification_defaults")


def _config_view(cfg: dict, role: str) -> dict:
    """Verifiers never see parameters or key refs (design §8.1)."""
    if role == "verifier":
        return {k: cfg[k] for k in _VERIFIER_CONFIG_FIELDS}
    return cfg


@app.get("/v1/configs")
def list_all_configs(model: str | None = None, status: str | None = None,
                     p=Depends(require("registry.read"))):
    statuses = (status,) if status else None
    cfgs = STATE["registry"].list_configs(model_id=model, statuses=statuses)
    return [_config_view(c, p[1]) for c in cfgs]


@app.get("/v1/configs/{config_id}")
def get_config(config_id: str, p=Depends(require("registry.read"))):
    cfg = STATE["registry"].get_config(config_id)
    if not cfg:
        raise HTTPException(404)
    return _config_view(cfg, p[1])


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
        sorted((c for c in reg.list_configs(model_id=model_id, statuses=("active",))
                if c["model_version"] == version),
               key=lambda c: c["config_version"], reverse=True)   # newest wins after rotation
    cfg = next((c for c in cfgs if c and c["model_id"] == model_id and c["model_version"] == version), None)
    if cfg is None:
        raise HTTPException(404, "no matching config (pass ?config_id= for a draft config)")
    if cfg["status"] in ("deprecated", "retired"):
        raise HTTPException(409, f"config is {cfg['status']}; it must not mark new text")
    key_row = reg.get_key(cfg["key_ref"])
    if key_row["state"] != "active":
        raise HTTPException(409, f"key is {key_row['state']}; it must not mark new text")
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


# ---- key lifecycle (design §6 / §8.1; POC 4.1) -------------------------------------------
class KeyIn(BaseModel):
    purpose: str = Field(default="generation", pattern=r"^generation$",
                         description="signing identities are system-managed, not created here")
    owner_model_id: str | None = None


class RevokeIn(BaseModel):
    reason: str = Field(min_length=3)


@app.post("/v1/keys", status_code=201)
def create_key(body: KeyIn, p=Depends(require("key.write"))):
    import uuid
    reg: RegistryStore = STATE["registry"]
    lineage = str(uuid.uuid4())
    ref = vault().create(KeySpec("generation", body.owner_model_id, lineage,
                                 STATE["policy"]["custody_default"]))
    row = reg.add_key(lineage, "generation", body.owner_model_id, ref.backend, ref.ref,
                      ref.version, lineage, p[0])
    STATE["audit"].append({"principal": p[0], "role": p[1]}, "key.create",
                          {"kind": "key", "id": lineage}, details={"backend": ref.backend})
    return row


@app.get("/v1/keys")
def list_keys(lineage: str | None = None, _=Depends(require("key.read"))):
    return STATE["registry"].list_keys(lineage)


@app.get("/v1/keys/{key_id}")
def get_key(key_id: str, _=Depends(require("key.read"))):
    row = STATE["registry"].get_key(key_id)
    if not row:
        raise HTTPException(404)
    return row


@app.post("/v1/keys/{key_id}:rotate")
def rotate_key(key_id: str, p=Depends(require("key.write"))):
    """Design §6 data flow 3: new backend key version; old key verifies forever
    (verify_until stays null); each active config is deprecated and superseded
    by a new ACTIVE config version on the new key, inheriting the accepted
    calibration (the null distribution under human text is key-independent)."""
    import uuid
    reg: RegistryStore = STATE["registry"]
    old = reg.get_key(key_id)
    if old is None:
        raise HTTPException(404)
    if old["purpose"] != "generation" or old["state"] != "active":
        raise HTTPException(409, f"only active generation keys rotate (state={old['state']})")

    ref = backend_for(old["backend"]).rotate(BackendRef(old["backend"], old["backend_ref"], old["key_version"]))
    now = datetime.now(UTC).isoformat()
    reg.set_key_state(key_id, "verify-only", generate_until=now)
    new_key_id = str(uuid.uuid4())
    new_row = reg.add_key(new_key_id, "generation", old["owner_model_id"], ref.backend,
                          ref.ref, ref.version, old["lineage_id"], p[0])
    STATE["audit"].append({"principal": p[0], "role": p[1]}, "key.rotate",
                          {"kind": "key", "id": key_id},
                          details={"new_key_id": new_key_id, "new_version": ref.version,
                                   "lineage": old["lineage_id"]})

    rotated_configs = []
    for cfg in reg.configs_using_key(key_id, statuses=("active",)):
        reg.transition(cfg["config_id"], "deprecated")
        new_cfg = reg.clone_config_for_rotation(cfg["config_id"], new_key_id, p[0])
        if cfg["config_id"] in STATE["null_models"]:
            STATE["null_models"][new_cfg["config_id"]] = STATE["null_models"][cfg["config_id"]]
        STATE["audit"].append({"principal": p[0], "role": p[1]}, "config.create",
                              {"kind": "config", "id": new_cfg["config_id"]},
                              details={"supersedes": cfg["config_id"], "cause": "key.rotate"})
        rotated_configs.append({"old": cfg["config_id"], "new": new_cfg["config_id"]})
    return {"old_key": reg.get_key(key_id), "new_key": new_row, "configs": rotated_configs}


@app.post("/v1/keys/{key_id}:revoke")
def revoke_key(key_id: str, body: RevokeIn, p=Depends(require("key.destroy"))):
    """Compromise response (design §6): generation stops at once; verification
    continues forever, flagged historical_key — a leaked key means a positive
    could be spoofed after the leak date, and the result page says so."""
    reg: RegistryStore = STATE["registry"]
    old = reg.get_key(key_id)
    if old is None:
        raise HTTPException(404)
    if old["state"] in ("revoked", "destroyed"):
        raise HTTPException(409, f"key already {old['state']}")
    backend_for(old["backend"]).revoke(BackendRef(old["backend"], old["backend_ref"], old["key_version"]), body.reason)
    now = datetime.now(UTC).isoformat()
    row = reg.set_key_state(key_id, "revoked", generate_until=now, revoked_at=now,
                            revocation_reason=body.reason)
    for cfg in reg.configs_using_key(key_id, statuses=("active",)):
        reg.transition(cfg["config_id"], "deprecated")   # stops marking; stays scanned + flagged
    STATE["audit"].append({"principal": p[0], "role": p[1]}, "key.revoke",
                          {"kind": "key", "id": key_id}, details={"reason": body.reason})
    return row


@app.post("/v1/keys/{key_id}:destroy")
def destroy_key(key_id: str, p=Depends(require("key.destroy"))):
    """Destruction erases material and ends verification for that key — only
    allowed once the record's verify_until has passed (design §6: default is
    verify forever, so destruction is a deliberate, dated policy decision)."""
    reg: RegistryStore = STATE["registry"]
    old = reg.get_key(key_id)
    if old is None:
        raise HTTPException(404)
    vu = old["verify_until"]
    if vu is None or vu > datetime.now(UTC).isoformat():
        raise HTTPException(409, "verify_until has not passed (null = verify forever); "
                                 "set a verify_until policy before destroying material")
    backend_for(old["backend"]).destroy(BackendRef(old["backend"], old["backend_ref"], old["key_version"]))
    row = reg.set_key_state(key_id, "destroyed")
    STATE["audit"].append({"principal": p[0], "role": p[1]}, "key.destroy",
                          {"kind": "key", "id": key_id})
    return row


# ---- published detectors (design §6.4; POC 4.5) -------------------------------------------
@app.post("/v1/configs/{config_id}/detector:publish")
def publish_detector(config_id: str, p=Depends(require("registry.write"))):
    """Export the scheme's publishable detector to the artifact store. For
    SynthID this DISCLOSES THE KEY LIST: the config is labelled
    public-detector and every later result carries spoofing_not_excluded."""
    import json as _json
    reg: RegistryStore = STATE["registry"]
    cfg = reg.get_config(config_id)
    if not cfg:
        raise HTTPException(404)
    adapter = schemes.get(cfg["scheme_id"])
    try:
        det = adapter.export_detector(cfg["scheme_params"], _key_handle(cfg))
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e
    if det is None:
        raise HTTPException(409, f"scheme {cfg['scheme_id']} cannot separate detection from the key")
    blob = _json.dumps({"kind": det.kind, "scheme_id": det.scheme_id, "sha256": det.sha256,
                        "payload": det.payload}, sort_keys=True).encode()
    uri = STATE["artifacts"].put("detectors",
                                 f"{cfg['model_id']}-{cfg['model_version']}-c{cfg['config_version']}",
                                 blob)
    cfg = reg.set_detector(config_id, uri)
    STATE["audit"].append({"principal": p[0], "role": p[1]}, "detector.publish",
                          {"kind": "config", "id": config_id},
                          details={"detector_ref": uri, "sha256": det.sha256,
                                   "disclosure": "public-detector"})
    return {"config_id": config_id, "detector_ref": uri, "sha256": det.sha256,
            "labels": cfg["labels"],
            "note": "the detector discloses key material for this scheme; results under this "
                    "config now carry spoofing_not_excluded"}


@app.get("/v1/detectors/{config_id}")
def get_detector(config_id: str, _=Depends(require("verify"))):
    """The published artifact is public by definition — any authenticated role."""
    import json as _json
    cfg = STATE["registry"].get_config(config_id)
    if not cfg or not cfg["detector_ref"]:
        raise HTTPException(404, "no published detector for this config")
    return _json.loads(STATE["artifacts"].get(cfg["detector_ref"]))


# ---- verification ----------------------------------------------------------------------
class VerifyIn(BaseModel):
    text: str | None = None
    token_ids: list[int] | None = None     # design §8.1: body is {text} or {token_ids}
    config_ids: list[str] | None = None
    fpr_target: float | None = Field(default=None, gt=0, le=0.05, description="optional override; it can only lower the deployment value")


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
    """Derive this config's scheme material through the key backend. Only
    generation keys may mark or detect — a signing key in a config is a wiring
    bug, refused outright (design §6: the purposes never mix)."""
    key_row = STATE["registry"].get_key(cfg["key_ref"])
    if key_row is None:
        raise HTTPException(409, f"config {cfg['config_id']} references unknown key {cfg['key_ref']}")
    if key_row["purpose"] != "generation":
        raise HTTPException(409, f"key {cfg['key_ref']} has purpose={key_row['purpose']}; "
                                 "only generation keys may mark or detect")
    try:
        material = backend_for(key_row["backend"]).derive(
            BackendRef(key_row["backend"], key_row["backend_ref"], key_row["key_version"]),
            f"markwright/root/{cfg['key_ref']}", 32)
    except PermissionError as e:   # destroyed key version
        raise HTTPException(409, str(e)) from e
    return KeyHandle(cfg["key_ref"], key_row["key_version"], key_row["backend"],
                     key_row["backend_ref"], material)


def _candidates(config_ids: list[str] | None, text_input: bool) -> list[Candidate]:
    """Candidate assembly shared by single and batch verify. Uncalibrated
    configs are never scanned; tokenizer-swap detection (POC 3.1) applies only
    to text input — token-ids submissions bypass the tokenizer by construction."""
    reg: RegistryStore = STATE["registry"]
    cfgs = [reg.get_config(c) for c in config_ids] if config_ids \
        else reg.list_configs(statuses=("active", "deprecated"))
    cands: list[Candidate] = []
    for cfg in cfgs:
        if not cfg or cfg["config_id"] not in STATE["null_models"]:
            continue
        key_row = reg.get_key(cfg["key_ref"])
        flags = ["historical_key"] if cfg["status"] == "deprecated" \
            or (key_row and key_row["state"] != "active") else []
        if cfg["labels"].get("disclosure") == "public-detector":
            flags.append("spoofing_not_excluded")
        if text_input:
            recorded = cfg["verification_defaults"].get("tokenizer_hash")
            if recorded is not None and _current_tokenizer_hash(cfg["tokenizer_ref"]) != recorded:
                flags.append("tokenizer_uncertain")
        cands.append(Candidate(cfg["config_id"], cfg["scheme_id"], schemes.get(cfg["scheme_id"]), cfg["scheme_params"],
                               _key_handle(cfg), cfg["tokenizer_ref"], STATE["null_models"][cfg["config_id"]],
                               cfg["verification_defaults"]["min_tokens"], cfg["verification_defaults"].get("min_detectable_tokens"),
                               key_row["key_version"], flags))
    return cands


def _digest_salt() -> bytes:
    return os.environ.get("MARKWRIGHT_DIGEST_SALT", "dev-salt").encode()


@app.post("/v1/verify")
def verify_text(v: VerifyIn, p=Depends(require("verify"))):
    if (v.text is None) == (v.token_ids is None):
        raise HTTPException(422, "provide exactly one of text or token_ids")
    fpr = min(STATE["policy"]["fpr_target"], v.fpr_target or 1.0)
    cands = _candidates(v.config_ids, text_input=v.text is not None)
    res = verify(v.text if v.text is not None else v.token_ids, cands, fpr,
                 count_tokens=_count_tokens, deployment_salt=_digest_salt(), requested_by=p[0])
    STATE["registry"].save_result(res.payload)
    STATE["audit"].append({"principal": p[0], "role": p[1]}, "verify.single", {"kind": "result", "id": res["result_id"]},
                          details={"band": res["band"], "token_count": res["token_count"], "candidates": len(cands)})
    return res.payload


class BatchVerifyIn(BaseModel):
    texts: list[str] | None = Field(default=None, max_length=1000)
    token_ids: list[list[int]] | None = Field(default=None, max_length=1000)
    config_ids: list[str] | None = None
    fpr_target: float | None = Field(default=None, gt=0, le=0.05)


@app.post("/v1/verify/batch")
def verify_batch(v: BatchVerifyIn, p=Depends(require("verify"))):
    """Up to 1,000 inputs. The summary reports the EXPECTED number of chance
    positives (n × fpr_target) next to the observed count, so a reviewer sees
    '7 flagged; about 0.5 expected by chance' rather than a bare 7 (design §7.4).
    POC runs synchronously; v1 moves batches to the jobs worker."""
    import uuid
    if (v.texts is None) == (v.token_ids is None):
        raise HTTPException(422, "provide exactly one of texts or token_ids")
    inputs: list = v.texts if v.texts is not None else v.token_ids
    if not inputs:
        raise HTTPException(422, "empty batch")
    batch_id = str(uuid.uuid4())
    fpr = min(STATE["policy"]["fpr_target"], v.fpr_target or 1.0)
    cands = _candidates(v.config_ids, text_input=v.texts is not None)
    results = []
    for item in inputs:
        res = verify(item, cands, fpr, count_tokens=_count_tokens,
                     deployment_salt=_digest_salt(), requested_by=p[0])
        res.payload["request_id"] = batch_id
        STATE["registry"].save_result(res.payload)
        results.append(res.payload)
    bands: dict[str, int] = {}
    for r in results:
        bands[r["band"]] = bands.get(r["band"], 0) + 1
    positives = sum(bands.get(b, 0) for b in ("strong_evidence", "moderate_evidence"))
    summary = {
        "n": len(results), "bands": bands, "fpr_target": fpr,
        "positives_at_or_above_moderate": positives,
        "expected_false_positives": len(results) * fpr,
        "note": f"{positives} flagged; about {len(results) * fpr:.2g} expected by chance "
                f"at fpr_target={fpr:.1e} if nothing were marked",
    }
    STATE["audit"].append({"principal": p[0], "role": p[1]}, "verify.batch",
                          {"kind": "batch", "id": batch_id},
                          details={"n": len(results), "bands": bands})
    return {"batch_id": batch_id, "summary": summary, "results": results}


@app.get("/v1/verify/results")
def list_results(since: str | None = None, band: str | None = None, model: str | None = None,
                 p=Depends(require("verify"))):
    """Result metadata, never text. Verifiers and operators see their own
    requests; auditors and admins see the deployment's (design §8.1)."""
    own = p[0] if p[1] in ("verifier", "operator") else None
    return STATE["registry"].list_results(since=since, band=band, model_id=model, requested_by=own)


# ---- offline bundle export (design §7.5; POC phase 5) -------------------------------------
@app.get("/v1/bundle")
def bundle_export(model: str | None = None, p=Depends(require("registry.write"))):
    """Everything an offline verifier needs, nothing secret: config metadata,
    null models, published detectors. Configs without a published detector are
    included with their skip reason — the offline CLI reports them, never
    silently drops them."""
    import json as _json

    from fastapi.responses import Response

    from markwright.verify.offline import build_bundle
    reg: RegistryStore = STATE["registry"]
    entries = []
    for cfg in reg.list_configs(model_id=model, statuses=("active", "deprecated")):
        null_model = None
        if cfg["calibration_ref"]:
            run = reg.get_calibration(cfg["calibration_ref"])
            if run and run["status"] == "accepted":
                null_model = run["report"]["null_model"]
        detector = None
        if cfg["detector_ref"]:
            detector = _json.loads(STATE["artifacts"].get(cfg["detector_ref"]))
        public = {k: cfg[k] for k in ("config_id", "model_id", "model_version", "config_version",
                                      "scheme_id", "scheme_version", "status", "tokenizer_ref",
                                      "verification_defaults", "labels")}
        entries.append({"config": public, "null_model": null_model, "detector": detector})
    if not entries:
        raise HTTPException(404, "no active or deprecated configs to export")
    data = build_bundle(entries, STATE["policy"]["fpr_target"])
    STATE["audit"].append({"principal": p[0], "role": p[1]}, "export.evidence",
                          {"kind": "bundle", "id": model or "all"},
                          details={"configs": len(entries)})
    return Response(content=data, media_type="application/gzip",
                    headers={"Content-Disposition": "attachment; filename=markwright-verify.bundle"})


# ---- audit / policy / health -----------------------------------------------------------
@app.get("/v1/audit")
def audit(_=Depends(require("audit.read"))):
    return {"head": STATE["audit"].head, "entries": STATE["audit"].entries}


@app.post("/v1/audit/export")
def audit_export(p=Depends(require("audit.read"))):
    from fastapi.responses import Response
    STATE["audit"].append({"principal": p[0], "role": p[1]}, "export.evidence",
                          {"kind": "chain", "id": STATE["audit"].head})
    data = export_bundle(STATE["audit"], __version__)
    return Response(content=data, media_type="application/gzip",
                    headers={"Content-Disposition": "attachment; filename=markwright-audit.tgz"})


@app.get("/v1/audit/public-key")
def audit_public_key(_=Depends(require("registry.read"))):
    pub = STATE["audit"].public_key_bytes
    if pub is None:
        raise HTTPException(503, "no deployment signing key")
    return {"algorithm": "ed25519", "public_key_hex": pub.hex()}


class PolicyIn(BaseModel):
    fpr_target: float | None = Field(default=None, gt=0, le=0.05)
    retention_days: int | None = Field(default=None, ge=0)


@app.put("/v1/policy")
def put_policy(body: PolicyIn, p=Depends(require("policy.write"))):
    before = dict(STATE["policy"])
    if body.fpr_target is not None:
        STATE["policy"]["fpr_target"] = body.fpr_target
    if body.retention_days is not None:
        STATE["policy"]["retention_days"] = body.retention_days
    STATE["audit"].append({"principal": p[0], "role": p[1]}, "policy.change",
                          {"kind": "policy", "id": "deployment"},
                          details={"before": before, "after": dict(STATE["policy"])})
    return STATE["policy"]


@app.get("/v1/policy")
def get_policy(_=Depends(require("registry.read"))):
    return STATE["policy"]


@app.get("/healthz")
def healthz():
    return {"ok": True, "version": __version__}


@app.get("/readyz")
def readyz():
    """Liveness is healthz; readiness proves the stores actually answer."""
    try:
        STATE["registry"].db.execute("SELECT 1")
        n_schemes = len(schemes.available())
    except Exception as e:
        raise HTTPException(503, f"not ready: {e}") from e
    return {"ready": True, "schemes": n_schemes, "profile": PROFILE,
            "calibrated_configs": len(STATE["null_models"])}


@app.get("/metrics")
def metrics():
    """Minimal Prometheus exposition (full OTel is v1, design §9)."""
    from fastapi.responses import PlainTextResponse
    reg: RegistryStore = STATE["registry"]
    lines = [
        "# TYPE markwright_results_total counter",
    ]
    for r in reg.result_counts_by_band():
        lines.append(f'markwright_results_total{{band="{r["band"]}"}} {r["n"]}')
    lines.append("# TYPE markwright_configs gauge")
    for r in reg.config_counts_by_status():
        lines.append(f'markwright_configs{{status="{r["status"]}"}} {r["n"]}')
    lines.append("# TYPE markwright_audit_entries gauge")
    lines.append(f"markwright_audit_entries {len(STATE['audit'].entries)}")
    return PlainTextResponse("\n".join(lines) + "\n")
