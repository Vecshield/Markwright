"""Offline verification against an exported bundle (design §7.5, POC phase 5).

A bundle is a tar.gz of everything a verifier needs and NOTHING secret:
config metadata, fitted null models, and published detector artifacts. Keyed
configs without a published detector cannot be verified offline — they are
listed in the bundle as ``skipped`` with the reason, never silently dropped.

No network call is ever made here. Text input needs the config's tokenizer in
the local HF cache; token-ids input needs nothing beyond the scheme adapter.
"""
from __future__ import annotations

import io
import json
import tarfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from markwright import __version__, schemes
from markwright.schemes.base import Detector, TokenIds
from markwright.stats.null_model import NullModel
from markwright.verify.pipeline import Candidate, Result, verify

BUNDLE_KIND = "markwright-verify-bundle"


def build_bundle(configs: list[dict[str, Any]], fpr_target: float) -> bytes:
    """Server-side assembly. Each entry: {config: <public fields>, null_model:
    <dict> | None, detector: <dict> | None}. Configs lacking either are kept
    with a skip reason so the offline verifier can report them honestly."""
    manifest = {
        "kind": BUNDLE_KIND,
        "generated_at": datetime.now(UTC).isoformat(),
        "markwright_version": __version__,
        "fpr_target": fpr_target,
        "configs": configs,
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = json.dumps(manifest, sort_keys=True).encode()
        info = tarfile.TarInfo("bundle.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@dataclass
class LoadedBundle:
    fpr_target: float
    candidates: list[Candidate]
    skipped: list[dict[str, str]]       # {config_id, reason}
    generated_at: str


def load_bundle(path: str | Path) -> LoadedBundle:
    with tarfile.open(path, mode="r:gz") as tar:
        manifest = json.loads(tar.extractfile("bundle.json").read())
    if manifest.get("kind") != BUNDLE_KIND:
        raise ValueError(f"not a markwright verify bundle: {path}")

    candidates: list[Candidate] = []
    skipped: list[dict[str, str]] = []
    for entry in manifest["configs"]:
        cfg = entry["config"]
        if entry.get("null_model") is None:
            skipped.append({"config_id": cfg["config_id"], "reason": "no accepted calibration in bundle"})
            continue
        if entry.get("detector") is None:
            skipped.append({"config_id": cfg["config_id"],
                            "reason": "no published detector; keyed verification needs the server"})
            continue
        det = entry["detector"]
        detector = Detector(det["kind"], det["scheme_id"], det["payload"], det["sha256"])
        flags = ["spoofing_not_excluded"]                       # a published detector is public
        if cfg["status"] in ("deprecated", "retired"):
            flags.append("historical_key")
        vd = cfg["verification_defaults"]
        candidates.append(Candidate(
            cfg["config_id"], cfg["scheme_id"], schemes.get(cfg["scheme_id"]),
            det["payload"].get("params", {}), detector, cfg["tokenizer_ref"],
            NullModel.from_dict(entry["null_model"]),
            vd.get("min_tokens", 50), vd.get("min_detectable_tokens"),
            det["payload"].get("key_version", 1), flags))
    return LoadedBundle(manifest["fpr_target"], candidates, skipped, manifest["generated_at"])


def _count_tokens_local(text: str, tokenizer_ref: str) -> int:
    """Local HF cache only — offline means offline."""
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(tokenizer_ref, local_files_only=True)
    return len(tok(text, add_special_tokens=False)["input_ids"])


def verify_offline(bundle_path: str | Path, text: str | TokenIds,
                   fpr_target: float | None = None) -> tuple[Result, LoadedBundle]:
    """Run the standard pipeline from a bundle, no server, no network. The
    caller may only TIGHTEN the bundle's FPR target, same rule as the API."""
    bundle = load_bundle(bundle_path)
    fpr = min(bundle.fpr_target, fpr_target or 1.0)
    result = verify(text, bundle.candidates, fpr,
                    count_tokens=_count_tokens_local, deployment_salt=None,
                    requested_by=None, mode="offline")
    return result, bundle
