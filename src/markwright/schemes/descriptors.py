"""OWM scheme-descriptor discovery and validation.

A descriptor is the machine-readable identity card of a scheme
(schemas/scheme-descriptor.json is normative). Discovery order:

1. the adapter package itself: ``<scheme_id>.owm.json`` next to the adapter's
   module, so third-party scheme packages ship their own descriptor;
2. the built-in registry directory ``markwright/schemes/descriptors/``.

Loading never crashes the API over a bad third-party file: an invalid
descriptor is reported through :func:`descriptor_problems` and otherwise
treated as absent.
"""
from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

from markwright import schemes

_SCHEMA_PATH = Path(__file__).resolve().parents[3].parent / "schemas" / "scheme-descriptor.json"
_PACKAGED_SCHEMA = "scheme-descriptor.json"


@lru_cache(maxsize=1)
def descriptor_schema() -> dict[str, Any]:
    """The normative JSON Schema, from the repo in editable installs or the
    packaged copy inside the wheel."""
    repo = Path(__file__).resolve().parents[3] / "schemas" / _PACKAGED_SCHEMA
    if repo.exists():
        return json.loads(repo.read_text(encoding="utf-8"))
    packaged = resources.files("markwright") / "resources" / "schemas" / _PACKAGED_SCHEMA
    return json.loads(packaged.read_text(encoding="utf-8"))


def _candidate_files(scheme_id: str) -> list[Any]:
    out: list[Any] = []
    cls = schemes.available().get(scheme_id)
    if cls is not None:
        pkg = cls.__module__.rsplit(".", 1)[0] if "." in cls.__module__ else cls.__module__
        try:
            root = resources.files(pkg)
            out.append(root / f"{scheme_id}.owm.json")
            out.append(root / "descriptors" / f"{scheme_id}.owm.json")
        except (ModuleNotFoundError, TypeError):
            pass
    out.append(resources.files("markwright.schemes") / "descriptors" / f"{scheme_id}.owm.json")
    return out


def descriptor_for(scheme_id: str) -> dict[str, Any] | None:
    """The validated descriptor for a scheme, or None when absent or invalid."""
    doc, _ = _load(scheme_id)
    return doc


def descriptor_problems(scheme_id: str) -> str | None:
    """Why a descriptor is unavailable, for surfacing instead of swallowing."""
    _, problem = _load(scheme_id)
    return problem


def _load(scheme_id: str) -> tuple[dict[str, Any] | None, str | None]:
    import jsonschema

    last_problem: str | None = f"no descriptor file found for '{scheme_id}'"
    for f in _candidate_files(scheme_id):
        try:
            if not f.is_file():
                continue
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            last_problem = f"unreadable descriptor for '{scheme_id}': {e}"
            continue
        try:
            jsonschema.validate(doc, descriptor_schema())
        except jsonschema.ValidationError as e:
            return None, f"descriptor for '{scheme_id}' does not conform to OWM 0.1: {e.message}"
        if doc["scheme_id"] != scheme_id:
            return None, (f"descriptor file claims scheme_id '{doc['scheme_id']}' "
                          f"but was resolved for '{scheme_id}'")
        return doc, None
    return None, last_problem
