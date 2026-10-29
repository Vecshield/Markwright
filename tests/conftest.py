"""Shared test infrastructure.

Two adapters replace the baseline's invented-constant StubAdapter (banned by
CLAUDE.md):

- ``ReplayAdapter`` replays *recorded real* SynthID detections from
  ``tests/fixtures/detections/`` (regenerate with fixtures/regenerate.py).
  Tier-1 honesty tests run against genuine statistics without torch.
- ``ParityAdapter`` is a real, torch-free miniature scheme (statistic = mean
  token-id parity). It genuinely computes over inputs, so full-stack tier-1
  tests (calibrate → accept → activate → verify over real HTTP routes) exercise
  the entire pipeline with honest math. "Marked" parity inputs are sequences
  biased toward even ids.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from markwright.schemes.base import Capabilities, Detection, Detector, KeyHandle, TokenIds
from markwright.stats.null_model import NullModel

FIXTURES = Path(__file__).parent / "fixtures"


# ---- recorded-real fixtures ---------------------------------------------------------------
def load_token_ids(name: str) -> list[int]:
    return json.loads((FIXTURES / "token_ids" / f"{name}.json").read_text())["ids"]


def load_detection(case: str, key: str = "k1") -> Detection:
    d = json.loads((FIXTURES / "detections" / f"{case}.{key}.json").read_text())
    return Detection(d["statistic_name"], d["statistic_value"], d["scored_tokens"],
                     d["analytic_p_value"], d["min_detectable_tokens"], d["extra"], d["notes"])


@pytest.fixture(scope="session")
def real_null_model() -> NullModel:
    """The exact null model a calibration run over gutenberg-en-v1 produced."""
    return NullModel.from_dict(json.loads(
        (FIXTURES / "null_model.synthid.gutenberg-en-v1.json").read_text()))


class ReplayAdapter:
    """Replays recorded real SynthID detections, keyed by input length."""
    scheme_id, scheme_version, capabilities = "synthid-text", "replay", Capabilities()

    def __init__(self) -> None:
        self._by_len: dict[tuple[int, int], Detection] = {}
        for f in (FIXTURES / "detections").glob("*.json"):
            d = json.loads(f.read_text())
            case = json.loads((FIXTURES / "token_ids" / f"{d['case']}.json").read_text())
            self._by_len[(case["n"], d["key_version"])] = load_detection(d["case"], f"k{d['key_version']}")

    def params_schema(self) -> dict:
        return {"type": "object"}

    def default_params(self) -> dict:
        return {"min_tokens": 50}

    def detect(self, text: str | TokenIds, params: dict, key, tokenizer_ref: str) -> Detection:
        kv = getattr(key, "key_version", 1)
        got = self._by_len.get((len(text), kv))
        if got is None:
            raise KeyError(f"no recorded detection for length {len(text)} key v{kv}; "
                           "use a fixture input (tests/fixtures/token_ids)")
        return got


class ParityAdapter:
    """Minimal REAL scheme: statistic = mean(id % 2). Marked input = even-biased ids."""
    scheme_id, scheme_version = "parity-test", "0.0.1"
    capabilities = Capabilities(distortion_free=False, publishable_detector=False)

    def params_schema(self) -> dict:
        return {"type": "object", "properties": {"min_tokens": {"type": "integer", "default": 50}},
                "additionalProperties": True}

    def default_params(self) -> dict:
        return {"min_tokens": 50}

    def generate_config(self, params, key, tokenizer_ref):  # pragma: no cover - unused in tests
        raise NotImplementedError

    def apply(self, config):  # pragma: no cover - unused in tests
        raise NotImplementedError

    def detect(self, text: str | TokenIds, params: dict, key, tokenizer_ref: str) -> Detection:
        if isinstance(text, str):
            raise RuntimeError("parity-test is ids-only")
        vals = [i % 2 == 0 for i in text]
        stat = sum(vals) / len(vals) if vals else 0.5
        return Detection("even_fraction", stat, len(vals))

    def calibrate(self, *a, **k):  # pragma: no cover - the service orchestrates instead
        raise NotImplementedError

    def export_detector(self, params, key):
        """Parity detection needs no secret at all, so the detector is trivially
        publishable — which is exactly what the detector-publish and offline-
        bundle tests need to run without torch."""
        import hashlib
        import json as _json

        payload = {"scheme_id": self.scheme_id, "params": params,
                   "key_version": getattr(key, "key_version", 1)}
        digest = hashlib.sha256(_json.dumps(payload, sort_keys=True).encode()).hexdigest()
        return Detector(kind="artifact", scheme_id=self.scheme_id, payload=payload, sha256=digest)


def parity_marked_ids(n: int, even_fraction: float = 0.8, seed: int = 0) -> list[int]:
    """Deterministic 'marked' input for the parity scheme."""
    import random

    rng = random.Random(seed)
    return [rng.randrange(0, 25000) * 2 + (0 if rng.random() < even_fraction else 1)
            for _ in range(n)]


FIXTURE_KEY_V1 = KeyHandle("fixture-key", 1, "local-sealed", "fixture", sealed_material=b"\x01" * 32)
FIXTURE_KEY_V2 = KeyHandle("fixture-key", 2, "local-sealed", "fixture", sealed_material=b"\x02" * 32)


# ---- API fixture --------------------------------------------------------------------------
@pytest.fixture()
def api(tmp_path, monkeypatch):
    """A fresh in-process API: clean registry/vault/audit, parity scheme
    registered alongside the real ones. Returns (client, app_module)."""
    from fastapi.testclient import TestClient

    import markwright.schemes as schemes_mod
    from markwright.api import app as a
    from markwright.artifacts import LocalArtifactStore
    from markwright.keys.local_sealed import LocalSealedVault
    from markwright.registry import RegistryStore

    real_get = schemes_mod.get
    real_available = schemes_mod.available

    def patched_get(scheme_id: str):
        if scheme_id == "parity-test":
            return ParityAdapter()
        return real_get(scheme_id)

    def patched_available():
        return {**real_available(), "parity-test": ParityAdapter}

    monkeypatch.setattr(schemes_mod, "get", patched_get)
    monkeypatch.setattr(schemes_mod, "available", patched_available)

    a.STATE["registry"] = RegistryStore(tmp_path / "registry.sqlite")
    a.STATE["vault"] = LocalSealedVault(tmp_path / "vault.json", "test-passphrase")
    a.STATE["backends"] = {}
    a.STATE["oidc"] = None
    a.STATE["artifacts"] = LocalArtifactStore(tmp_path / "artifacts")
    a.STATE["null_models"] = {}
    a.STATE["tokenizer_hashes"] = {}
    a.STATE["policy"] = {"fpr_target": 1e-2, "retention_days": 90, "custody_default": "single"}
    a._init_audit()    # persistent signed chain bound to the fresh registry db
    return TestClient(a.app), a


OP = {"Authorization": "Bearer operator-token"}
ADMIN = {"Authorization": "Bearer admin-token"}
VERIFIER = {"Authorization": "Bearer verifier-token"}
AUDITOR = {"Authorization": "Bearer auditor-token"}


def bootstrap_parity_config(client, activate: bool = True) -> str:
    """model -> version -> parity config -> calibrate on the real corpus ->
    accept -> (activate). All through real HTTP routes; returns config_id."""
    assert client.post("/v1/models", json={"model_id": "acme-llm"}, headers=OP).status_code == 201
    assert client.post("/v1/models/acme-llm/versions",
                       json={"model_version": "4.1", "tokenizer_ref": "gpt2"}, headers=OP).status_code == 201
    cfg = client.post("/v1/models/acme-llm/versions/4.1/configs",
                      json={"scheme_id": "parity-test"}, headers=OP)
    assert cfg.status_code == 201, cfg.text
    config_id = cfg.json()["config_id"]
    run = client.post(f"/v1/configs/{config_id}/calibrations",
                      json={"null_corpus_ids": ["gutenberg-en-v1"]}, headers=OP)
    assert run.status_code == 201, run.text
    assert client.post(f"/v1/calibrations/{run.json()['run_id']}:accept", headers=OP).status_code == 200
    if activate:
        assert client.post(f"/v1/configs/{config_id}:activate", headers=OP).status_code == 200
    return config_id
