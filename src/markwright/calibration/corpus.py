"""Bundled null-corpus discovery and loading.

Corpora are JSONL files of pre-tokenized passages (see data/null/README.md).
Resolution order: $MARKWRIGHT_NULL_CORPORA, the repo's data/null/ (editable
install), then the packaged copy inside the wheel.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CorpusDoc:
    id: str
    source: str
    ids: list[int]

    @property
    def n(self) -> int:
        return len(self.ids)


def _roots() -> list[Path]:
    roots: list[Path] = []
    if env := os.environ.get("MARKWRIGHT_NULL_CORPORA"):
        roots.append(Path(env))
    # editable install: this file is <repo>/src/markwright/calibration/corpus.py
    roots.append(Path(__file__).resolve().parents[3] / "data" / "null")
    # packaged copy (pyproject force-include)
    roots.append(Path(__file__).resolve().parents[1] / "resources" / "null_corpora")
    return roots


def available_corpora() -> dict[str, Path]:
    found: dict[str, Path] = {}
    for root in _roots():
        if root.is_dir():
            for p in sorted(root.glob("*.jsonl")):
                found.setdefault(p.stem, p)
    return found


def load_corpus(corpus_id: str) -> Iterator[CorpusDoc]:
    path = available_corpora().get(corpus_id)
    if path is None:
        raise KeyError(f"null corpus '{corpus_id}' not found (available: {sorted(available_corpora())})")
    with path.open() as f:
        for line in f:
            d = json.loads(line)
            yield CorpusDoc(d["id"], d["source"], d["ids"])


# Which tokenizer each bundled corpus was tokenized with. A calibration run
# refuses a corpus whose tokenizer differs from the config's tokenizer_ref —
# ids from the wrong tokenizer would fit a meaningless null (design §5 note 3).
CORPUS_TOKENIZER = {"gutenberg-en-v1": "gpt2"}
