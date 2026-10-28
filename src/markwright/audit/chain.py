"""Append-only, hash-chained audit log with Ed25519 anchors.

entry_hash = sha256(canonical_json(entry minus entry_hash, signature)).
Anchors sign the current head so an exported range verifies offline.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

GENESIS = "0" * 64
FORBIDDEN_DETAIL_KEYS = {"text", "prompt", "completion", "key_material", "secret", "passphrase"}


def canonical(entry: dict[str, Any]) -> bytes:
    e = {k: v for k, v in entry.items() if k not in ("entry_hash", "signature")}
    return json.dumps(e, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def entry_hash(entry: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(entry)).hexdigest()


class AuditChain:
    def __init__(self, signing_key: Ed25519PrivateKey | None = None, anchor_every: int = 100):
        self.entries: list[dict[str, Any]] = []
        self._sk = signing_key
        self.anchor_every = anchor_every

    @property
    def head(self) -> str:
        return self.entries[-1]["entry_hash"] if self.entries else GENESIS

    def append(self, actor: dict, action: str, target: dict, outcome: str = "ok", details: dict | None = None) -> dict:
        details = details or {}
        leaked = FORBIDDEN_DETAIL_KEYS & set(map(str.lower, details))
        if leaked:
            raise ValueError(f"audit details must never contain {sorted(leaked)}")
        entry = {
            "seq": len(self.entries),
            "ts": datetime.now(UTC).isoformat(),
            "actor": actor,
            "action": action,
            "target": target,
            "outcome": outcome,
            "details": details,
            "prev_hash": self.head,
            "signature": None,
        }
        entry["entry_hash"] = entry_hash(entry)
        self.entries.append(entry)
        if self._sk and (len(self.entries) % self.anchor_every == 0):
            self.anchor()
        return entry

    def anchor(self) -> dict:
        if not self._sk:
            raise RuntimeError("no signing key configured")
        entry = self.append({"principal": "system", "role": "system"}, "anchor", {"kind": "chain", "id": self.head})
        entry["signature"] = self._sk.sign(bytes.fromhex(entry["entry_hash"])).hex()
        return entry

    @staticmethod
    def verify(entries: Iterable[dict[str, Any]], public_key: Ed25519PublicKey | None = None) -> tuple[bool, str]:
        prev = GENESIS
        for e in entries:
            if e["prev_hash"] != prev:
                return False, f"chain break at seq {e['seq']}"
            if entry_hash(e) != e["entry_hash"]:
                return False, f"hash mismatch at seq {e['seq']}"
            if e["action"] == "anchor" and public_key is not None:
                if not e.get("signature"):
                    return False, f"unsigned anchor at seq {e['seq']}"
                try:
                    public_key.verify(bytes.fromhex(e["signature"]), bytes.fromhex(e["entry_hash"]))
                except Exception:
                    return False, f"bad anchor signature at seq {e['seq']}"
            prev = e["entry_hash"]
        return True, "ok"


def new_signing_key() -> tuple[Ed25519PrivateKey, bytes]:
    sk = Ed25519PrivateKey.generate()
    pub = sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return sk, pub
