"""Append-only, hash-chained audit log with Ed25519 anchors.

entry_hash = sha256(canonical_json(entry minus entry_hash, signature)).
Anchors sign the current head so an exported range verifies offline.

Persistence (POC plan 4.3): pass a sqlite3 connection and the chain writes
every entry to an ``audit_log`` table and reloads it on construction, so the
chain — and its tamper-evidence — survives process restarts. Signed evidence
bundles are built by :func:`export_bundle` and verified offline by
:func:`verify_bundle` (CLI: ``markwright audit export|verify``).
"""
from __future__ import annotations

import hashlib
import io
import json
import tarfile
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # avoid a hard registry import; any object with .execute(sql, params) works
    from markwright.registry.store import Database

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
    def __init__(self, signing_key: Ed25519PrivateKey | None = None, anchor_every: int = 100,
                 db: Database | None = None):
        self.entries: list[dict[str, Any]] = []
        self._sk = signing_key
        self.anchor_every = anchor_every
        self._db = db
        if db is not None:
            db.execute("CREATE TABLE IF NOT EXISTS audit_log (seq INTEGER PRIMARY KEY, entry TEXT)")
            self.entries = [json.loads(r["entry"]) for r in
                            db.execute("SELECT entry FROM audit_log ORDER BY seq")]

    @property
    def head(self) -> str:
        return self.entries[-1]["entry_hash"] if self.entries else GENESIS

    def sign(self, data: bytes) -> str | None:
        """Ed25519 signature (hex) with the deployment identity, or None when
        the chain has no signing key. Used for audit anchors, evidence bundles
        and OWM repo declarations alike: one identity, one verification story."""
        if self._sk is None:
            return None
        return self._sk.sign(data).hex()

    @property
    def public_key_bytes(self) -> bytes | None:
        if self._sk is None:
            return None
        return self._sk.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)

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
        self._persist(entry)
        if self._sk and (len(self.entries) % self.anchor_every == 0):
            self.anchor()
        return entry

    def anchor(self) -> dict:
        if not self._sk:
            raise RuntimeError("no signing key configured")
        entry = self.append({"principal": "system", "role": "system"}, "anchor", {"kind": "chain", "id": self.head})
        entry["signature"] = self._sk.sign(bytes.fromhex(entry["entry_hash"])).hex()
        self._persist(entry, update=True)   # re-write with the signature attached
        return entry

    def _persist(self, entry: dict[str, Any], update: bool = False) -> None:
        if self._db is None:
            return
        if update:
            self._db.execute("UPDATE audit_log SET entry=:e WHERE seq=:s",
                             {"e": json.dumps(entry), "s": entry["seq"]})
        else:
            self._db.execute("INSERT INTO audit_log (seq, entry) VALUES (:s, :e)",
                             {"s": entry["seq"], "e": json.dumps(entry)})

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


# ---- signed evidence bundles (design §4.4 / journey D) ---------------------------------------
def export_bundle(chain: AuditChain, markwright_version: str) -> bytes:
    """tar.gz: entries.jsonl + manifest.json + manifest.sig + deployment.pub.
    The signature is Ed25519 over the canonical manifest JSON; the manifest
    pins the chain head, so any entry tampering breaks either the hash chain
    or the head, and any head tampering breaks the signature."""
    if chain._sk is None:
        raise RuntimeError("no deployment signing key; cannot export a signed bundle")
    manifest = {
        "kind": "markwright-audit-export",
        "generated_at": datetime.now(UTC).isoformat(),
        "markwright_version": markwright_version,
        "n_entries": len(chain.entries),
        "head": chain.head,
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    sig = chain._sk.sign(manifest_bytes)

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        def add(name: str, data: bytes) -> None:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        add("entries.jsonl", "\n".join(json.dumps(e, sort_keys=True) for e in chain.entries).encode())
        add("manifest.json", manifest_bytes)
        add("manifest.sig", sig.hex().encode())
        add("deployment.pub", chain.public_key_bytes.hex().encode())
    return buf.getvalue()


def verify_bundle(data: bytes, public_key_hex: str | None = None) -> tuple[bool, str]:
    """Offline verification of an exported bundle: signature over the manifest,
    head matches the entries, and the hash chain holds. ``public_key_hex``
    overrides the bundled key (pass the out-of-band copy to detect bundle-level
    key substitution)."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        read = {m.name: tar.extractfile(m).read() for m in tar.getmembers()}
    manifest = json.loads(read["manifest.json"])
    entries = [json.loads(line) for line in read["entries.jsonl"].decode().splitlines()]
    pub_hex = public_key_hex or read["deployment.pub"].decode().strip()
    pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
    try:
        pub.verify(bytes.fromhex(read["manifest.sig"].decode().strip()), read["manifest.json"])
    except Exception:
        return False, "manifest signature invalid"
    if len(entries) != manifest["n_entries"]:
        return False, f"entry count mismatch ({len(entries)} != {manifest['n_entries']})"
    if (entries[-1]["entry_hash"] if entries else GENESIS) != manifest["head"]:
        return False, "head does not match the last entry"
    ok, msg = AuditChain.verify(entries, pub)
    return (ok, "ok: signature, head and chain verified" if ok else msg)
