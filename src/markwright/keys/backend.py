"""KeyBackend protocol. Implementations: local_sealed (MVP), vault_transit,
aws_kms, gcp_kms, azure_keyvault, pkcs11 (v1/v2)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class KeySpec:
    purpose: str            # "generation" | "signing"
    owner_model_id: str | None
    lineage_id: str
    custody_mode: str = "single"


@dataclass(frozen=True)
class BackendRef:
    backend: str
    ref: str
    version: int


class KeyBackend(Protocol):
    name: str

    def create(self, spec: KeySpec) -> BackendRef: ...
    def rotate(self, ref: BackendRef) -> BackendRef: ...
    def derive(self, ref: BackendRef, info: str, length: int) -> bytes:
        """Derived material for a scheme. Backends that can derive inside their
        boundary do so; others export-under-wrap to the key service only."""
        ...
    def mac(self, ref: BackendRef, data: bytes) -> bytes | None: ...
    def revoke(self, ref: BackendRef, reason: str) -> None: ...
    def destroy(self, ref: BackendRef) -> None: ...
