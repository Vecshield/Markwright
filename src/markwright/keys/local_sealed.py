"""Local sealed vault: the air-gapped / development KeyBackend.

A JSON file of key versions, each a 32-byte root secret encrypted with
AES-256-GCM under a master key derived from a passphrase (scrypt). This is
*single-operator custody* and the UI says so. Production sites use Vault,
a cloud KMS or an HSM through the same interface.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .backend import BackendRef, KeySpec
from .derive import hkdf


class LocalSealedVault:
    name = "local-sealed"

    def __init__(self, path: str | os.PathLike, passphrase: str):
        self.path = Path(path)
        self._pass = passphrase.encode()
        if not self.path.exists():
            salt = secrets.token_bytes(16)
            self._write({"salt": salt.hex(), "keys": {}})
        self._doc = json.loads(self.path.read_text(encoding="utf-8"))
        self._master = Scrypt(salt=bytes.fromhex(self._doc["salt"]), length=32, n=2**15, r=8, p=1).derive(self._pass)

    # -- storage -------------------------------------------------------------------
    def _write(self, doc: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def _seal(self, secret: bytes) -> dict:
        nonce = secrets.token_bytes(12)
        return {"nonce": nonce.hex(), "ct": AESGCM(self._master).encrypt(nonce, secret, b"markwright").hex()}

    def _open(self, blob: dict) -> bytes:
        return AESGCM(self._master).decrypt(bytes.fromhex(blob["nonce"]), bytes.fromhex(blob["ct"]), b"markwright")

    # -- KeyBackend ------------------------------------------------------------------
    def create(self, spec: KeySpec) -> BackendRef:
        entry = {"lineage": spec.lineage_id, "purpose": spec.purpose, "versions": [self._seal(secrets.token_bytes(32))],
                 "state": ["active"], "created": time.time()}
        self._doc["keys"][spec.lineage_id] = entry
        self._write(self._doc)
        return BackendRef(self.name, spec.lineage_id, 1)

    def rotate(self, ref: BackendRef) -> BackendRef:
        entry = self._doc["keys"][ref.ref]
        entry["versions"].append(self._seal(secrets.token_bytes(32)))
        entry["state"].append("active")
        self._write(self._doc)
        return BackendRef(self.name, ref.ref, len(entry["versions"]))

    def derive(self, ref: BackendRef, info: str, length: int) -> bytes:
        entry = self._doc["keys"][ref.ref]
        if entry["state"][ref.version - 1] == "destroyed":
            raise PermissionError("key version destroyed")
        root = self._open(entry["versions"][ref.version - 1])
        return hkdf(root, info, length)

    def mac(self, ref: BackendRef, data: bytes) -> bytes | None:
        import hashlib
        import hmac

        root = self._open(self._doc["keys"][ref.ref]["versions"][ref.version - 1])
        return hmac.new(root, data, hashlib.sha256).digest()

    def revoke(self, ref: BackendRef, reason: str) -> None:
        self._doc["keys"][ref.ref]["state"][ref.version - 1] = f"revoked:{reason}"
        self._write(self._doc)

    def destroy(self, ref: BackendRef) -> None:
        entry = self._doc["keys"][ref.ref]
        entry["versions"][ref.version - 1] = {"nonce": "", "ct": ""}
        entry["state"][ref.version - 1] = "destroyed"
        self._write(self._doc)
