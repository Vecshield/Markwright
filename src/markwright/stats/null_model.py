"""Calibrated null model: empirical CDF body + generalised-Pareto tail.

Fitted per length bucket on a human-text (null) corpus. Maps a detector
statistic to a p-value with a bootstrap interval. Uses scipy only.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import stats

TAIL_QUANTILE = 0.99
MIN_TAIL_POINTS = 30


@dataclass
class NullBucket:
    length: int                     # nominal token length of the bucket
    n: int
    knots: list[float]              # sorted null statistics (subsampled if large)
    tail_threshold: float | None    # u; above this the GPD applies
    gpd_shape: float | None         # xi
    gpd_scale: float | None         # sigma
    tail_frac: float                # P(S > u) under the null

    def p_value(self, s: float) -> float:
        """One-sided P(S >= s | H0). Empirical below the threshold, GPD above."""
        k = np.asarray(self.knots)
        if self.tail_threshold is not None and s > self.tail_threshold:
            exceed = stats.genpareto.sf(s - self.tail_threshold, c=self.gpd_shape, scale=self.gpd_scale)
            return float(max(self.tail_frac * exceed, 1.0 / (self.n + 1) * 1e-3))
        # add-one smoothing so an extreme sample never yields exactly 0
        return float((np.sum(k >= s) + 1) / (len(k) + 1))


@dataclass
class NullModel:
    statistic_name: str
    buckets: list[NullBucket] = field(default_factory=list)
    corpus_ids: list[str] = field(default_factory=list)

    def p_value(self, s: float, token_count: int) -> float:
        """Interpolate (in log p) between the two nearest length buckets."""
        if not self.buckets:
            raise ValueError("null model has no buckets")
        b = sorted(self.buckets, key=lambda x: x.length)
        if token_count <= b[0].length:
            return b[0].p_value(s)
        if token_count >= b[-1].length:
            return b[-1].p_value(s)
        lo = max(x for x in b if x.length <= token_count)
        hi = min(x for x in b if x.length >= token_count)
        if lo is hi:
            return lo.p_value(s)
        w = (token_count - lo.length) / (hi.length - lo.length)
        lp = (1 - w) * np.log(lo.p_value(s)) + w * np.log(hi.p_value(s))
        return float(np.exp(lp))

    def p_value_interval(self, s: float, token_count: int, n_boot: int = 200, seed: int = 0) -> tuple[float, float]:
        """95% bootstrap interval on p from resampling the null knots of the nearest bucket."""
        rng = np.random.default_rng(seed)
        b = min(self.buckets, key=lambda x: abs(x.length - token_count))
        ps = []
        for _ in range(n_boot):
            resampled = rng.choice(b.knots, size=len(b.knots), replace=True)
            ps.append(fit_bucket(resampled.tolist(), b.length).p_value(s))
        lo, hi = np.percentile(ps, [2.5, 97.5])
        return float(lo), float(hi)

    def to_dict(self) -> dict:
        return {
            "statistic_name": self.statistic_name,
            "corpus_ids": self.corpus_ids,
            "buckets": [b.__dict__ for b in self.buckets],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "NullModel":
        return cls(d["statistic_name"], [NullBucket(**b) for b in d["buckets"]], d.get("corpus_ids", []))


def fit_bucket(values: list[float], length: int, max_knots: int = 5000) -> NullBucket:
    v = np.sort(np.asarray(values, dtype=float))
    n = len(v)
    u = float(np.quantile(v, TAIL_QUANTILE)) if n >= MIN_TAIL_POINTS * 10 else None
    shape = scale = None
    tail_frac = 1 - TAIL_QUANTILE
    if u is not None:
        exc = v[v > u] - u
        if len(exc) >= MIN_TAIL_POINTS and np.ptp(exc) > 0:
            shape, _, scale = stats.genpareto.fit(exc, floc=0)
            tail_frac = len(exc) / n
        else:
            u = None
    knots = v if n <= max_knots else np.quantile(v, np.linspace(0, 1, max_knots))
    return NullBucket(length, n, knots.tolist(), u, shape, scale, tail_frac)
