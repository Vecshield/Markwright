import pytest

from markwright.audit import AuditChain, new_signing_key
from markwright.keys.backend import KeySpec
from markwright.keys.derive import derive_int_keys
from markwright.keys.local_sealed import LocalSealedVault


def test_chain_detects_tampering_and_verifies_anchor_signature():
    sk, pub_raw = new_signing_key()
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    pub = Ed25519PublicKey.from_public_bytes(pub_raw)
    ch = AuditChain(sk, anchor_every=3)
    for i in range(5):
        ch.append({"principal": "op", "role": "operator"}, "config.create", {"kind": "config", "id": str(i)})
    ok, msg = AuditChain.verify(ch.entries, pub)
    assert ok, msg
    ch.entries[1]["details"] = {"changed": True}
    ok, msg = AuditChain.verify(ch.entries, pub)
    assert not ok and "seq 1" in msg


def test_audit_refuses_text_and_secrets():
    ch = AuditChain()
    with pytest.raises(ValueError):
        ch.append({"principal": "x", "role": "verifier"}, "verify.single", {"kind": "result", "id": "r"}, details={"text": "..."})


def test_local_vault_roundtrip_and_rotation(tmp_path):
    v = LocalSealedVault(tmp_path / "vault.json", "pass")
    ref = v.create(KeySpec("generation", "m", "lineage-1"))
    a = v.derive(ref, "markwright/synthid-text/cfg/v1", 32)
    assert a == v.derive(ref, "markwright/synthid-text/cfg/v1", 32)          # deterministic
    ref2 = v.rotate(ref)
    assert ref2.version == 2 and v.derive(ref2, "markwright/synthid-text/cfg/v1", 32) != a
    # reopening with the passphrase yields the same material; secrets never sit in plaintext on disk
    v2 = LocalSealedVault(tmp_path / "vault.json", "pass")
    assert v2.derive(ref, "markwright/synthid-text/cfg/v1", 32) == a
    assert a.hex() not in (tmp_path / "vault.json").read_text()
    keys = derive_int_keys(a, "markwright/synthid-text/k/v1", 30)
    assert len(keys) == 30 and all(0 <= k < 2**31 for k in keys)
