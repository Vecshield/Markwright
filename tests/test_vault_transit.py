"""Integration tests for the Vault Transit KeyBackend against a REAL
OpenBao/Vault dev server (marker: network — excluded from CI tiers 1/2):

    docker run -d --rm --name mw-openbao -p 8200:8200 \\
        -e BAO_DEV_ROOT_TOKEN_ID=dev-root openbao/openbao:2

Everything here proves the §6 rule: the database stores handles, the backend
holds bytes — derivation happens inside Vault (transit/hmac), the root key is
non-exportable, and the whole control plane runs unchanged on this backend.
"""
from __future__ import annotations

import os

import httpx
import pytest

pytestmark = pytest.mark.network

ADDR = os.environ.get("MARKWRIGHT_VAULT_ADDR", "http://127.0.0.1:8200")
TOKEN = os.environ.get("MARKWRIGHT_VAULT_TOKEN", "dev-root")


def _bao_up() -> bool:
    try:
        return httpx.get(f"{ADDR}/v1/sys/health", timeout=2).status_code in (200, 429, 473, 501, 503)
    except Exception:
        return False


if not _bao_up():
    pytest.skip("no OpenBao/Vault dev server on :8200", allow_module_level=True)


@pytest.fixture(scope="module")
def backend():
    import hvac

    from markwright.keys.vault_transit import VaultTransitBackend

    admin = hvac.Client(url=ADDR, token=TOKEN)
    mounts = admin.sys.list_mounted_secrets_engines()
    if "transit/" not in mounts:
        admin.sys.enable_secrets_engine("transit")
    return VaultTransitBackend(addr=ADDR, token=TOKEN, prefix="mw-test")


from markwright.keys.backend import KeySpec  # noqa: E402


def test_create_derive_is_deterministic_and_boundary_kept(backend):
    ref = backend.create(KeySpec("generation", "m", "lineage-vt-1"))
    assert ref.backend == "vault-transit" and ref.version == 1
    a = backend.derive(ref, "markwright/synthid-text/cfg/v1", 32)
    assert len(a) == 32
    assert a == backend.derive(ref, "markwright/synthid-text/cfg/v1", 32)   # deterministic
    assert a != backend.derive(ref, "markwright/synthid-text/cfg/v2", 32)   # info-separated
    # the derived bytes feed schemes exactly like the local vault's
    from markwright.keys.derive import derive_int_keys
    keys = derive_int_keys(a, "markwright/synthid-text/k/v1", 30)
    assert len(keys) == 30 and all(0 <= k < 2**31 for k in keys)
    # root key is non-exportable: Vault refuses to hand out the material
    info = backend.client.secrets.transit.read_key(name="mw-test-lineage-vt-1")
    assert info["data"]["exportable"] is False


def test_expand_beyond_one_hmac_block(backend):
    ref = backend.create(KeySpec("generation", "m", "lineage-vt-2"))
    okm = backend.derive(ref, "markwright/kgw/long", 80)      # 3 HMAC blocks
    assert len(okm) == 80
    assert okm[:32] != okm[32:64]                             # counter-mode blocks differ
    assert okm == backend.derive(ref, "markwright/kgw/long", 80)
    assert okm[:32] != backend.derive(ref, "markwright/kgw/longer", 32)


def test_rotation_versions_are_independent_and_old_keeps_working(backend):
    # Transit create on an existing key is a no-op and the dev server keeps
    # state between test runs, so assert RELATIVE versions, not absolutes.
    ref1 = backend.create(KeySpec("generation", "m", "lineage-vt-3"))
    v1 = backend.derive(ref1, "x", 32)
    ref2 = backend.rotate(ref1)
    assert ref2.version > ref1.version
    v2 = backend.derive(ref2, "x", 32)
    assert v1 != v2
    assert backend.derive(ref1, "x", 32) == v1                # old version verifies forever
    ref3 = backend.rotate(ref2)
    assert ref3.version == ref2.version + 1                   # strictly monotone per rotate


def test_mac_inside_boundary(backend):
    ref = backend.create(KeySpec("generation", "m", "lineage-vt-4"))
    m1 = backend.mac(ref, b"payload")
    assert len(m1) == 32 and m1 == backend.mac(ref, b"payload") and m1 != backend.mac(ref, b"other")


def test_destroy_deletes_the_vault_key(backend):
    ref = backend.create(KeySpec("generation", "m", "lineage-vt-5"))
    backend.derive(ref, "x", 32)
    backend.destroy(ref)
    with pytest.raises(Exception):
        backend.derive(ref, "x", 32)


def test_full_control_plane_on_vault_transit(api, backend, monkeypatch):
    """The entire parity loop — config, calibration, activation, verification,
    signed audit identity — with every secret held by Vault, not the local
    sealed file. Nothing above the KeyBackend interface changed."""
    from .conftest import OP, VERIFIER, bootstrap_parity_config, parity_marked_ids
    client, a = api
    a.STATE["vault"] = backend
    # drop the fixture's local-sealed signing identity so a fresh one is
    # created IN VAULT (a real deployment starts with its final backend)
    a.STATE["registry"].db.execute("DELETE FROM keys WHERE purpose='signing'")
    a.STATE["registry"].db.execute("DELETE FROM audit_log")
    a._init_audit()                        # deployment signing seed derived in Vault too
    config_id = bootstrap_parity_config(client)
    key_row = client.get(f"/v1/configs/{config_id}", headers=OP).json()
    assert client.get(f"/v1/keys/{key_row['key_ref']}", headers=OP).json()["backend"] == "vault-transit"
    res = client.post("/v1/verify", json={"token_ids": parity_marked_ids(300)}, headers=VERIFIER).json()
    assert res["band"] == "moderate_evidence"
    r = client.post("/v1/audit/export", headers={"Authorization": "Bearer auditor-token"})
    from markwright.audit import verify_bundle
    assert verify_bundle(r.content)[0]