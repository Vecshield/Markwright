"""ArtifactStore: published detectors, calibration reports, evidence bundles.

One interface (design §3 backends); the POC ships the local-filesystem driver.
S3/MinIO implements the same protocol in v1 — callers hold opaque ``local://``
or ``s3://`` URIs and never touch paths directly.

Writes are atomic (tmp + rename) and content-hashed; ``get`` re-hashes and
refuses corrupted artifacts rather than returning bad bytes.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Protocol


class ArtifactStore(Protocol):
    def put(self, kind: str, name: str, data: bytes) -> str:
        """Store bytes, return an opaque URI."""
        ...

    def get(self, uri: str) -> bytes: ...

    def sha256(self, uri: str) -> str: ...


class LocalArtifactStore:
    """Filesystem driver. Layout: <root>/<kind>/<name>.<sha256[:12]>"""

    scheme = "local://"

    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, kind: str, name: str, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        rel = Path(kind) / f"{name}.{digest[:12]}"
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        return f"{self.scheme}{rel.as_posix()}"

    def _path(self, uri: str) -> Path:
        if not uri.startswith(self.scheme):
            raise ValueError(f"not a local artifact uri: {uri}")
        raw_rel = uri.removeprefix(self.scheme).replace("\\", "/")
        rel = Path(raw_rel)
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError(f"unsafe artifact uri: {uri}")
        return self.root / rel

    def get(self, uri: str) -> bytes:
        path = self._path(uri)
        data = path.read_bytes()
        expected = path.name.rsplit(".", 1)[-1]
        if hashlib.sha256(data).hexdigest()[: len(expected)] != expected:
            raise OSError(f"artifact corrupted on disk: {uri}")
        return data

    def sha256(self, uri: str) -> str:
        return hashlib.sha256(self.get(uri)).hexdigest()
