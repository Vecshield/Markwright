"""HashiCorp Vault / OpenBao Transit KeyBackend (design §6.1, on-prem default).

The rule that holds in both profiles: the database stores handles, the backend
holds bytes. Here the backend is Vault's transit engine; the root key is a
non-exportable HMAC key and ALL derivation happens inside Vault's boundary via
``transit/hmac`` — scheme material is HKDF-Expand (RFC 5869 §2.3) where the
PRF invocation is Vault's, so the root secret never leaves the server. Nothing
custom: HKDF-Expand is counter-mode HMAC composition exactly as specified.

Setup (the compose onprem profile does this automatically in v1):

    bao secrets enable transit     # or: vault secrets enable transit
    MARKWRIGHT_VAULT_ADDR=http://127.0.0.1:8200 MARKWRIGHT_VAULT_TOKEN=...

Lifecycle mapping:
- create  -> transit key (type hmac, non-exportable)
- rotate  -> transit key rotation (old versions keep verifying: min_decryption_version stays 1)
- revoke  -> registry-side state only; Vault has no revoked-but-usable notion,
             and revoked keys MUST keep verifying (design §6) — the registry
             enforces that generation stops
- destroy -> trim old versions + deletion_allowed, then delete when the whole
             lineage is destroyed
"""
from __future__ import annotations

import base64
import os

from .backend import BackendRef, KeySpec


class VaultTransitBackend:
    name = "vault-transit"

    def __init__(self, addr: str | None = None, token: str | None = None,
                 mount: str = "transit", prefix: str = "markwright"):
        import hvac

        self.client = hvac.Client(
            url=addr or os.environ.get("MARKWRIGHT_VAULT_ADDR", "http://127.0.0.1:8200"),
            token=token or os.environ.get("MARKWRIGHT_VAULT_TOKEN"))
        if not self.client.is_authenticated():   # fail at construction, not first use
            raise PermissionError("vault: not authenticated (set MARKWRIGHT_VAULT_TOKEN)")
        self.mount = mount
        self.prefix = prefix

    def _key_name(self, lineage: str) -> str:
        return f"{self.prefix}-{lineage}"

    # -- KeyBackend -------------------------------------------------------------------
    def create(self, spec: KeySpec) -> BackendRef:
        # aes256-gcm96: Vault maintains a distinct per-version HMAC key for
        # every transit key, which is what transit/hmac uses. (hvac's client-side
        # validation rejects the newer dedicated `hmac` key type.)
        self.client.secrets.transit.create_key(
            name=self._key_name(spec.lineage_id), key_type="aes256-gcm96",
            exportable=False, mount_point=self.mount)
        return BackendRef(self.name, spec.lineage_id, 1)

    def rotate(self, ref: BackendRef) -> BackendRef:
        self.client.secrets.transit.rotate_key(name=self._key_name(ref.ref), mount_point=self.mount)
        info = self.client.secrets.transit.read_key(name=self._key_name(ref.ref), mount_point=self.mount)
        return BackendRef(self.name, ref.ref, int(info["data"]["latest_version"]))

    def _hmac(self, ref: BackendRef, data: bytes) -> bytes:
        out = self.client.secrets.transit.generate_hmac(
            name=self._key_name(ref.ref), hash_input=base64.b64encode(data).decode(),
            algorithm="sha2-256", key_version=ref.version, mount_point=self.mount)
        # "vault:v<N>:<base64 mac>"
        return base64.b64decode(out["data"]["hmac"].split(":", 2)[2])

    def derive(self, ref: BackendRef, info: str, length: int) -> bytes:
        """HKDF-Expand with Vault's HMAC as the PRF — derivation never leaves
        the boundary (design §6.1 table row: 'yes (transit/derive, datakey)')."""
        okm = b""
        block = b""
        counter = 1
        while len(okm) < length:
            block = self._hmac(ref, block + info.encode() + bytes([counter]))
            okm += block
            counter += 1
        return okm[:length]

    def mac(self, ref: BackendRef, data: bytes) -> bytes:
        return self._hmac(ref, data)

    def revoke(self, ref: BackendRef, reason: str) -> None:
        # Vault-side nothing to do: revoked keys must KEEP verifying (design §6);
        # the registry's key state is what stops generation. Config update keeps
        # old versions decodable on purpose.
        return None

    def destroy(self, ref: BackendRef) -> None:
        name = self._key_name(ref.ref)
        self.client.secrets.transit.update_key_configuration(
            name=name, deletion_allowed=True, mount_point=self.mount)
        self.client.secrets.transit.delete_key(name=name, mount_point=self.mount)
