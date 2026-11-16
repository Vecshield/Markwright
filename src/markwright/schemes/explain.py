"""Plain-language explanations of the watermarking schemes Markwright wraps.

One source of truth for the dashboard, the CLI and `GET /v1/schemes`. These
describe published work; the citations point at the papers so an operator can
check the claims rather than take ours. Entries exist for schemes that are not
installed, so the registry can explain what it would take to add them.

Adding a scheme package should add its entry here (or ship its own via the
``explanation`` attribute on the adapter class).
"""
from __future__ import annotations

from typing import Any

EXPLANATIONS: dict[str, dict[str, Any]] = {
    "synthid-text": {
        "title": "SynthID-Text (Google DeepMind)",
        "citation": "Dathathri et al., Nature 2024",
        "status": "installed with the synthid extra",
        "how_it_marks":
            "At each step the sampler runs a tournament between candidate tokens. The "
            "winner of each round is decided by a pseudo-random g-value keyed to the "
            "preceding n-gram, so marked text wins more of those tournaments than chance "
            "would explain. The text stays fluent because every candidate was already an "
            "acceptable next token.",
        "how_it_detects":
            "Recompute the g-values for the same n-grams and average them over the tokens "
            "that count (repeated contexts are skipped). Unmarked text averages about 0.5; "
            "marked text sits measurably above it.",
        "statistic": "mean g-value across scored tokens",
        "key_material": "30 derived integers, computed inside the key backend from the root secret",
        "strengths": [
            "Quality impact is small because the tournament only reorders acceptable tokens",
            "Detection needs no model forward pass, only the token ids and the key list",
            "A trained Bayesian detector can be published for third parties",
        ],
        "limits": [
            "Not distortion-free: the sampling distribution does change",
            "Publishing the detector discloses the key list, so marks can then be forged",
            "Short or repetitive text leaves too few scorable tokens",
        ],
        "params": {
            "ngram_len": "context window that seeds each tournament (5 is the published default)",
            "num_keys": "tournament depth; more layers means a stronger signal per token",
            "sampling": "temperature / top-k / top-p recorded so a run can be reproduced",
        },
    },
    "kgw": {
        "title": "KGW green-list (Kirchenbauer et al.)",
        "citation": "Kirchenbauer et al., ICML 2023",
        "status": "separate package: markwright-scheme-kgw",
        "how_it_marks":
            "A seed derived from the preceding token(s) splits the vocabulary into a green "
            "list (a gamma fraction) and the rest. The sampler adds a fixed bias delta to "
            "every green token's logit, so marked text over-uses green tokens.",
        "how_it_detects":
            "Recompute the green list for each position and count how many tokens landed in "
            "it. Under no watermark the share is gamma; the z-score measures the excess.",
        "statistic": "z-score of the green-token count",
        "key_material": "one 64-bit seed, derived inside the key backend",
        "strengths": [
            "Simple, well studied, and cheap to detect",
            "Strength is tunable: raising delta increases detectability",
        ],
        "limits": [
            "The bias changes word choice, and quality cost grows with delta",
            "No publishable detector: the green list IS the key, so verification needs "
            "either the key or a detect-only service run by the operator",
            "Repeated n-grams are skipped, so repetitive text scores fewer tokens",
        ],
        "params": {
            "gamma": "fraction of the vocabulary on the green list (0.25 default)",
            "delta": "logit bias added to green tokens; the quality/detectability dial",
            "seeding_scheme": "how the seed is derived from context (lefthash or selfhash)",
            "vocab_size": "must match the tokenizer, since green lists are per-vocabulary",
        },
    },
    "unigram": {
        "title": "Unigram / Zhao",
        "citation": "Zhao et al., 2023",
        "status": "planned (MarkLLM)",
        "how_it_marks":
            "One fixed green list for the whole vocabulary, with no dependence on context.",
        "how_it_detects": "Same z-test as KGW, with one global list.",
        "statistic": "z-score of the green-token count",
        "key_material": "a single seed fixing the global list",
        "strengths": ["More robust to editing, because no context means no broken n-grams"],
        "limits": ["A leaked list is trivially spoofable, so results always carry that caveat"],
        "params": {"gamma": "green-list fraction", "delta": "logit bias"},
    },
    "kuditipudi-exp": {
        "title": "Kuditipudi EXP / ITS (distortion-free)",
        "citation": "Kuditipudi et al., 2023",
        "status": "planned (MarkLLM)",
        "how_it_marks":
            "Sampling is driven by a pseudo-random key sequence instead of fresh randomness, "
            "so the output distribution is unchanged over the whole key.",
        "how_it_detects":
            "Align the text against the key sequence and compare the alignment cost with "
            "permutations of the key, which gives an empirical p-value.",
        "statistic": "alignment cost against the key sequence (permutation test)",
        "key_material": "a PRNG seed regenerating the key sequence; never stored",
        "strengths": ["Distortion-free: text quality is provably unaffected",
                      "Edit-robust variants exist (EXP-Edit, ITS-Edit)"],
        "limits": ["Detection is a permutation test, so it costs far more compute",
                   "Single-sample detection is weaker than biased schemes"],
        "params": {"key_length": "length of the key sequence", "n_runs": "permutation count"},
    },
}


def explanation_for(scheme_id: str) -> dict[str, Any] | None:
    return EXPLANATIONS.get(scheme_id)


def catalogue(installed: set[str]) -> list[dict[str, Any]]:
    """Every scheme we can describe, flagged with whether it is installed here."""
    return [{**v, "scheme_id": k, "installed": k in installed}
            for k, v in EXPLANATIONS.items()]
