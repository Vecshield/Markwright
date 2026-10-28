"""Regenerate all test fixtures. Fixtures are ARTIFACTS, never hand-edited.

    .venv/bin/python tests/fixtures/regenerate.py

Needs torch+transformers (the `synthid` extra) but NO model and NO network:
marked sequences are produced by driving the SynthID logits processor over
uniform base logits — the watermark's own bias is the only signal, which is
exactly what detection measures. Unmarked sequences come from the bundled null
corpus. Everything is seeded; reruns are deterministic for a given
transformers version (the meta file records versions so drift shows up as a
reviewable diff, not silent test rot).

Outputs:
    token_ids/{marked,unmarked}_*.json   — inputs for tier-2 (real adapter)
    detections/*.json                    — recorded real Detections for tier-1 ReplayAdapter
    meta.json                            — versions + parameters of this run
"""
from __future__ import annotations

import json
import platform
from dataclasses import asdict
from pathlib import Path

HERE = Path(__file__).parent

# Deterministic fixture keys: never used outside tests.
ROOT_KEY_V1 = b"\x01" * 32
ROOT_KEY_V2 = b"\x02" * 32
SEED = 0
VOCAB = 50_000          # < gpt2 vocab size; ids stay decodable
TEMPERATURE = 0.7
MARKED_LENGTHS = (120, 250, 450)
UNMARKED_PICKS = {100: 3, 200: 3, 400: 3}   # docs per bucket from the null corpus


def marked_sequence(processor, n: int, seed: int) -> list[int]:
    """Drive the watermark's logits bias over uniform scores — no LM needed."""
    import torch

    torch.manual_seed(seed)
    ids = torch.randint(100, VOCAB, (1, 6))
    for _ in range(n):
        scores = torch.zeros(1, VOCAB)
        biased = processor(ids, scores.clone())
        nxt = torch.multinomial(torch.softmax(biased / TEMPERATURE, -1), 1)
        ids = torch.cat([ids, nxt], dim=1)
    return ids[0].tolist()


def main() -> None:
    import torch
    import transformers

    from markwright.calibration import load_corpus
    from markwright.schemes.base import KeyHandle
    from markwright.schemes.synthid import SynthIDTextAdapter

    adapter = SynthIDTextAdapter()
    params = adapter.default_params()
    key1 = KeyHandle("fixture-key", 1, "local-sealed", "fixture", sealed_material=ROOT_KEY_V1)
    key2 = KeyHandle("fixture-key", 2, "local-sealed", "fixture", sealed_material=ROOT_KEY_V2)
    proc = adapter.apply(adapter.generate_config(params, key1, "gpt2"))

    (HERE / "token_ids").mkdir(parents=True, exist_ok=True)
    (HERE / "detections").mkdir(parents=True, exist_ok=True)

    cases: dict[str, list[int]] = {}
    for i, n in enumerate(MARKED_LENGTHS):
        cases[f"marked_{n}"] = marked_sequence(proc, n, SEED + i)

    picked: dict[int, int] = {k: 0 for k in UNMARKED_PICKS}
    for doc in load_corpus("gutenberg-en-v1"):
        bucket = min(UNMARKED_PICKS, key=lambda b: abs(b - doc.n))
        if picked[bucket] < UNMARKED_PICKS[bucket]:
            cases[f"unmarked_{bucket}_{picked[bucket]}"] = list(doc.ids)
            picked[bucket] += 1
        if all(picked[b] >= UNMARKED_PICKS[b] for b in picked):
            break

    for name, ids in cases.items():
        (HERE / "token_ids" / f"{name}.json").write_text(
            json.dumps({"name": name, "n": len(ids), "ids": ids}, separators=(",", ":")))

    # Record REAL detections (right key and wrong key) for the tier-1 ReplayAdapter.
    for name, ids in cases.items():
        for key, tag in ((key1, "k1"), (key2, "k2")):
            det = adapter.detect(ids, params, key, "gpt2")
            (HERE / "detections" / f"{name}.{tag}.json").write_text(
                json.dumps({"case": name, "key_version": key.key_version, **asdict(det)}, indent=1))

    (HERE / "meta.json").write_text(json.dumps({
        "seed": SEED, "vocab": VOCAB, "temperature": TEMPERATURE,
        "scheme_params": params,
        "torch": torch.__version__, "transformers": transformers.__version__,
        "python": platform.python_version(),
        "note": "marked sequences are watermark-bias-only (uniform base logits, no LM); "
                "unmarked sequences are gutenberg-en-v1 passages",
    }, indent=1))
    print(f"wrote {len(cases)} token-id cases and {2*len(cases)} detections under {HERE}")


if __name__ == "__main__":
    main()
