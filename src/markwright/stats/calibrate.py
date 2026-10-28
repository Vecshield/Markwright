"""Generic calibration helpers shared by all adapters."""
from __future__ import annotations

from collections import defaultdict

import numpy as np

from markwright.schemes.base import Detection

from .null_model import NullModel, fit_bucket, quality_for

LENGTH_BUCKETS = (50, 100, 200, 400, 800, 1600)


def _bucket_of(n: int) -> int:
    return min(LENGTH_BUCKETS, key=lambda b: abs(b - n))


def fit_null_by_length(
    null_detections: list[Detection],
    corpus_ids: list[str] | None = None,
    n_boot: int = 50,
) -> dict:
    """Group null statistics by nearest length bucket and fit each.

    ``n_boot`` bootstrap re-fits per bucket are precomputed here (calibration
    time) so verify-time interval queries are lookups, not GPD refits. Quality
    is derived from the per-bucket sample sizes (small corpus → provisional)."""
    by_len: dict[int, list[float]] = defaultdict(list)
    name = null_detections[0].statistic_name if null_detections else "statistic"
    for d in null_detections:
        if d.scored_tokens > 0:
            by_len[_bucket_of(d.scored_tokens)].append(d.statistic_value)
    kept = {L: vals for L, vals in sorted(by_len.items()) if len(vals) >= 20}
    model = NullModel(
        name,
        [fit_bucket(vals, L, n_boot=n_boot) for L, vals in kept.items()],
        corpus_ids=corpus_ids or [],
        quality=quality_for({L: len(v) for L, v in kept.items()}),
    )
    return model.to_dict()


def min_detectable_length(marked: list[Detection], null_model: dict, fpr_target: float, tpr_goal: float = 0.9) -> int | None:
    """Smallest length bucket where TPR >= tpr_goal at p <= fpr_target on unedited marked text."""
    nm = NullModel.from_dict(null_model)
    by_len: dict[int, list[bool]] = defaultdict(list)
    for d in marked:
        if d.scored_tokens > 0:
            p = nm.p_value(d.statistic_value, d.scored_tokens)
            by_len[_bucket_of(d.scored_tokens)].append(p <= fpr_target)
    for L in LENGTH_BUCKETS:
        hits = by_len.get(L, [])
        if len(hits) >= 20 and float(np.mean(hits)) >= tpr_goal:
            return L
    return None
