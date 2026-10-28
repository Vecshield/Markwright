"""Adapter discovery via entry points (group ``markwright.schemes``)."""
from __future__ import annotations

from importlib.metadata import entry_points

from .base import SchemeAdapter

_BUILTIN = {
    "synthid-text": "markwright.schemes.synthid:SynthIDTextAdapter",
}


def _load(path: str) -> type:
    mod, _, name = path.partition(":")
    import importlib

    return getattr(importlib.import_module(mod), name)


def available() -> dict[str, type]:
    found: dict[str, type] = {}
    for ep in entry_points(group="markwright.schemes"):
        try:
            found[ep.name] = ep.load()
        except Exception:  # adapter's optional deps missing; skip, don't crash
            continue
    for name, path in _BUILTIN.items():
        if name not in found:
            try:
                found[name] = _load(path)
            except Exception:
                continue
    return found


def get(scheme_id: str) -> SchemeAdapter:
    cls = available().get(scheme_id)
    if cls is None:
        raise KeyError(f"scheme '{scheme_id}' is not installed")
    return cls()
