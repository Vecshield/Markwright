"""Calibrated null model: empirical CDF body + generalised-Pareto tail.

Fitted per length bucket on a human-text (null) corpus. Maps a detector
statistic to a p-value with a bootstrap interval. Uses scipy only.

Honesty machinery (design §7.2 + POC plan phase 1.3):

- Every model knows its own **resolution floor** — the smallest p-value its
  null sample can support. Below the floor, a p-value is extrapolation, not
  measurement. ``band_for`` refuses STRONG/MODERATE bands when the deployment's
  FPR target is finer than the floor.
- Every model carries a **quality** tag: ``provisional`` (small corpus, e.g.
  the bundled starter corpora) or ``standard``. Provisional models put the
  ``provisional_calibration`` caveat on every result they touch.
- The 95% interval on p is **precomputed at calibration time** as a set of
  bootstrap re-fits per bucket, so verification does a few array lookups
  instead of 200 GPD fits per request.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import stats

TAIL_QUANTILE = 0.99
MIN_TAIL_POINTS = 30
# How far below the empirical resolution 1/(n+1) a fitted GPD tail is trusted
# to extrapolate. One sample of n points pins the CDF to ~1/n; the tail fit
# buys roughly three more orders of magnitude before the fit's own standard
# error dominates. Beyond that we clamp — never report p below the clamp.
TAIL_EXTRAPOLATION = 1e-3

QUALITY_PROVISIONAL = "provisional"
QUALITY_STANDARD = "standard"
# Below this many null documents in any bucket the whole model is provisional.
PROVISIONAL_BELOW_N = 2000


@dataclass
class NullBucket:
    length: int                     # nominal token length of the bucket
    n: int
    knots: list[float]              # sorted null statistics (subsampled if large)
    tail_threshold: float | None    # u; above this the GPD applies
    gpd_shape: float | None         # xi
    gpd_scale: float | None         # sigma
    tail_frac: float                # P(S > u) under the null
    boot: list["NullBucket"] = field(default_factory=list)   # bootstrap re-fits (no nested boot)

    @property
    def floor(self) -> float:
        """Smallest p this bucket can honestly report."""
        base = 1.0 / (self.n + 1)
        return base * TAIL_EXTRAPOLATION if self.tail_threshold is not None else base

    def p_value(self, s: float) -> float:
        """One-sided P(S >= s | H0). Empirical below the threshold, GPD above."""
        k = np.asarray(self.knots)
        if self.tail_threshold is not None and s > self.tail_threshold:
            exceed = stats.genpareto.sf(s - self.tail_threshold, c=self.gpd_shape, scale=self.gpd_scale)
            return float(max(self.tail_frac * exceed, self.floor))
        # add-one smoothing so an extreme sample never yields exactly 0
        return float((np.sum(k >= s) + 1) / (len(k) + 1))

    def to_dict(self) -> dict:
        return {
            "length": self.length, "n": self.n, "knots": self.knots,
            "tail_threshold": self.tail_threshold, "gpd_shape": self.gpd_shape,
            "gpd_scale": self.gpd_scale, "tail_frac": self.tail_frac,
            "boot": [b.to_dict() for b in self.boot],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "NullBucket":
        return cls(
            d["length"], d["n"], d["knots"], d["tail_threshold"],
            d["gpd_shape"], d["gpd_scale"], d["tail_frac"],
            [cls.from_dict(b) for b in d.get("boot", [])],
        )


@dataclass
class NullModel:
    statistic_name: str
    buckets: list[NullBucket] = field(default_factory=list)
    corpus_ids: list[str] = field(default_factory=list)
    quality: str = QUALITY_PROVISIONAL

    def resolution_floor(self) -> float:
        """The smallest p-value this calibration can support, taken as the WORST
        (largest) floor across buckets — conservative by design: a result's
        length bucket is data-dependent, so the guarantee must hold for all."""
        if not self.buckets:
            raise ValueError("null model has no buckets")
        return max(b.floor for b in self.buckets)

    def p_value(self, s: float, token_count: int) -> float:
        """Interpolate (in log p) between the two nearest length buckets."""
        if not self.buckets:
            raise ValueError("null model has no buckets")
        b = sorted(self.buckets, key=lambda x: x.length)
        if token_count <= b[0].length:
            return b[0].p_value(s)
        if token_count >= b[-1].length:
            return b[-1].p_value(s)
        lo = max((x for x in b if x.length <= token_count), key=lambda x: x.length)
        hi = min((x for x in b if x.length >= token_count), key=lambda x: x.length)
        if lo is hi:
            return lo.p_value(s)
        w = (token_count - lo.length) / (hi.length - lo.length)
        lp = (1 - w) * np.log(lo.p_value(s)) + w * np.log(hi.p_value(s))
        return float(np.exp(lp))

    def p_value_interval(self, s: float, token_count: int, n_boot: int = 200, seed: int = 0) -> tuple[float, float]:
        """95% bootstrap interval on p, from the nearest bucket.

        Fast path: the calibration job precomputed bootstrap re-fits
        (``bucket.boot``), so this is ~50 cheap evaluations. Fallback (no
        precomputed boot, e.g. hand-built models in tests): refit on the fly —
        correct but slow, never used on the API hot path after phase 1.4."""
        b = min(self.buckets, key=lambda x: abs(x.length - token_count))
        if b.boot:
            ps = [bb.p_value(s) for bb in b.boot]
        else:
            rng = np.random.default_rng(seed)
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
            "quality": self.quality,
            "buckets": [b.to_dict() for b in self.buckets],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "NullModel":
        return cls(
            d["statistic_name"],
            [NullBucket.from_dict(b) for b in d["buckets"]],
            d.get("corpus_ids", []),
            d.get("quality", QUALITY_PROVISIONAL),
        )


def fit_bucket(
    values: list[float],
    length: int,
    max_knots: int = 5000,
    n_boot: int = 0,
    boot_knots: int = 500,
    seed: int = 0,
) -> NullBucket:
    """Fit one length bucket. With ``n_boot > 0``, also fit that many bootstrap
    re-samples (subsampled to ``boot_knots`` knots each) and attach them, so
    interval queries at verify time are lookups, not refits."""
    bucket = _fit_one(values, length, max_knots)
    if n_boot > 0:
        rng = np.random.default_rng(seed)
        v = np.asarray(values, dtype=float)
        for _ in range(n_boot):
            resampled = rng.choice(v, size=len(v), replace=True)
            bucket.boot.append(_fit_one(resampled.tolist(), length, boot_knots))
    return bucket


def _fit_one(values: list[float], length: int, max_knots: int) -> NullBucket:
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


def quality_for(n_docs_per_bucket: dict[int, int]) -> str:
    """Model quality from corpus size: provisional unless every bucket has a
    comfortable sample. The POC's bundled corpora are always provisional."""
    if not n_docs_per_bucket or min(n_docs_per_bucket.values()) < PROVISIONAL_BELOW_N:
        return QUALITY_PROVISIONAL
    return QUALITY_STANDARD
