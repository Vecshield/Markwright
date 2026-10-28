"""Synchronous calibration run: null corpus → detections → fitted NullModel.

Produces the report dict persisted by the registry and shown by the CLI. The
POC path is null-corpus-only (no marked corpus, so no min_detectable_tokens and
no robustness sweep — those are the v1 calibration job, design §7.2); the
report says so in ``notes`` rather than leaving silent gaps.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from markwright.schemes.base import KeyHandle, SchemeAdapter
from markwright.stats.calibrate import fit_null_by_length
from markwright.stats.null_model import NullModel

from .corpus import CORPUS_TOKENIZER, load_corpus


def tokenizer_hash(tokenizer_ref: str) -> str | None:
    """Stable fingerprint of a tokenizer's vocabulary (not its files, which vary
    by cache layout). None when transformers is unavailable — recorded as
    unknown, and the verify path then treats text-input requests as uncertain."""
    try:
        from transformers import AutoTokenizer

        tok = AutoTokenizer.from_pretrained(tokenizer_ref)
        vocab = tok.get_vocab()
        canon = "\n".join(f"{t}\t{i}" for t, i in sorted(vocab.items(), key=lambda kv: kv[1]))
        return hashlib.sha256(canon.encode()).hexdigest()
    except Exception:
        return None


def run_calibration(
    adapter: SchemeAdapter,
    params: dict,
    key: KeyHandle,
    tokenizer_ref: str,
    null_corpus_ids: list[str],
    fpr_target: float,
) -> dict[str, Any]:
    """Run the adapter's detector over the bundled null corpora and fit the
    null model. Raises ValueError on a corpus/tokenizer mismatch."""
    for cid in null_corpus_ids:
        expected = CORPUS_TOKENIZER.get(cid)
        if expected is not None and expected != tokenizer_ref:
            raise ValueError(
                f"corpus '{cid}' is tokenized with '{expected}' but the config uses "
                f"'{tokenizer_ref}'; ids from the wrong tokenizer fit a meaningless null"
            )

    detections = []
    n_docs = 0
    for cid in null_corpus_ids:
        for doc in load_corpus(cid):
            detections.append(adapter.detect(doc.ids, params, key, tokenizer_ref))
            n_docs += 1
    if n_docs == 0:
        raise ValueError(f"no documents in corpora {null_corpus_ids}")

    null_model = fit_null_by_length(detections, corpus_ids=null_corpus_ids)
    nm = NullModel.from_dict(null_model)
    notes = [
        "null-corpus-only run: no marked corpus, so min_detectable_tokens and the "
        "robustness sweep are absent (v1 calibration job)",
        "analytic p assumes token independence; use calibrated p",
    ]
    if nm.quality != "standard":
        notes.insert(0, (
            f"PROVISIONAL: {n_docs} null docs; resolution floor "
            f"{nm.resolution_floor():.1e} — bands finer than this are refused"
        ))

    return {
        "run_id": str(uuid.uuid4()),
        "created_at": datetime.now(UTC).isoformat(),
        "null_corpus_ids": null_corpus_ids,
        "n_docs": n_docs,
        "scheme_id": adapter.scheme_id,
        "scheme_version": adapter.scheme_version,
        "tokenizer_ref": tokenizer_ref,
        "tokenizer_hash": tokenizer_hash(tokenizer_ref),
        "fpr_target": fpr_target,
        "quality": nm.quality,
        "resolution_floor": nm.resolution_floor(),
        "buckets": [{"length": b.length, "n": b.n, "has_tail": b.tail_threshold is not None}
                    for b in nm.buckets],
        "min_detectable_tokens": None,
        "robustness": {},
        "null_model": null_model,
        "notes": notes,
    }
