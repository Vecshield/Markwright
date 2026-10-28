"""Deterministic derivation of scheme material from a root secret (HKDF-SHA256).

Standard primitive from `cryptography`; nothing custom. The same (root, info)
always yields the same keys, so nothing derived needs to be stored.
"""
from __future__ import annotations

import struct

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def hkdf(root: bytes, info: str, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=None, info=info.encode()).derive(root)


def derive_int_keys(root: bytes, info: str, n: int) -> list[int]:
    """n non-negative 31-bit integers, e.g. the SynthID-Text ``keys`` list."""
    raw = hkdf(root, info, 4 * n)
    return [v & 0x7FFFFFFF for v in struct.unpack(f">{n}I", raw)]


def derive_seed(root: bytes, info: str) -> int:
    """One 64-bit seed, e.g. KGW ``hash_key`` or a Kuditipudi PRNG seed."""
    return struct.unpack(">Q", hkdf(root, info, 8))[0]
