"""The verification pipeline: gate -> candidates -> detect -> calibrate -> correct -> band.

Scheme-agnostic. The only scheme-specific call is ``adapter.detect``.
"""
from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from markwright import __version__
from markwright.schemes.base import Detection, Detector, KeyHandle, SchemeAdapter
from markwright.stats.bands import PLAIN_TEXT, Band, band_for, one_in_n
from markwright.stats.null_model import NullModel

ALWAYS_CAVEAT = "result_is_probabilistic"


@dataclass
class Candidate:
    config_id: str
    scheme_id: str
    adapter: SchemeAdapter
    params: dict
    key: KeyHandle | Detector
    tokenizer_ref: str
    null_model: NullModel
    min_tokens: int
    min_detectable_tokens: int | None
    key_version: int
    flags: list[str] = field(default_factory=list)   # e.g. historical_key, spoofing_not_excluded


@dataclass
class CandidateResult:
    config_id: str
    scheme_id: str
    key_version: int
    statistic: dict[str, Any]
    p_value: float
    p_value_adjusted: float
    ci_95: tuple[float, float] | None
    min_detectable_tokens: int | None
    null_model_ref: str


class Result:
    """Deliberately not a bool. ``if result:`` raises."""

    def __init__(self, payload: dict[str, Any]):
        self.payload = payload

    def __bool__(self):  # pragma: no cover
        raise TypeError("A verification result is probabilistic evidence, not a yes/no. Read .payload['band'].")

    def __getitem__(self, k):
        return self.payload[k]


def _digest(text: str, salt: bytes | None) -> str | None:
    if salt is None:
        return None
    norm = " ".join(text.split()).encode()
    return hmac.new(salt, norm, hashlib.sha256).hexdigest()


def verify(
    text: str,
    candidates: list[Candidate],
    fpr_target: float,
    *,
    count_tokens: Callable[[str, str], int],
    deployment_salt: bytes | None = None,
    requested_by: str | None = None,
    mode: str = "server",
) -> Result:
    now = datetime.now(timezone.utc).isoformat()
    caveats: set[str] = {ALWAYS_CAVEAT}
    results: list[CandidateResult] = []
    gate_ok = True
    token_count = 0
    scored_max = 0

    if not candidates:
        return Result(_payload(now, 0, 0, fpr_target, Band.INSUFFICIENT, "no candidate configurations",
                               None, [], sorted(caveats), requested_by, _digest(text, deployment_salt), mode))

    token_count = count_tokens(text, candidates[0].tokenizer_ref)
    k = len(candidates)
    if k > 1:
        caveats.add("multiple_candidates")

    for c in candidates:
        if token_count < c.min_tokens:
            caveats.add("short_text")
            continue
        det: Detection = c.adapter.detect(text, c.params, c.key, c.tokenizer_ref)
        if det.scored_tokens == 0:
            caveats.add("low_entropy")
            continue
        scored_max = max(scored_max, det.scored_tokens)
        p = c.null_model.p_value(det.statistic_value, det.scored_tokens)
        ci = c.null_model.p_value_interval(det.statistic_value, det.scored_tokens)
        results.append(CandidateResult(
            config_id=c.config_id, scheme_id=c.scheme_id, key_version=c.key_version,
            statistic={"name": det.statistic_name, "value": det.statistic_value,
                       "extra": {**det.extra, "analytic_p": det.analytic_p_value}},
            p_value=p, p_value_adjusted=min(1.0, k * p), ci_95=ci,
            min_detectable_tokens=c.min_detectable_tokens,
            null_model_ref=f"{c.config_id}:null:{c.null_model.statistic_name}",
        ))
        caveats.update(c.flags)

    if not results:
        gate_ok = False

    best = min(results, key=lambda r: r.p_value_adjusted) if results else None
    band, reason = band_for(best.p_value_adjusted if best else None, fpr_target, gate_ok)
    if band is Band.NONE:
        best = None
    if band in (Band.WEAK, Band.MODERATE, Band.STRONG):
        caveats.add("possible_paraphrase")  # a positive can still be a partially edited text

    return Result(_payload(now, token_count, scored_max, fpr_target, band, reason, best, results,
                           sorted(caveats), requested_by, _digest(text, deployment_salt), mode))


def _payload(now, token_count, scored, fpr_target, band, reason, best, results, caveats, requested_by, digest, mode):
    min_tokens = best.min_detectable_tokens if best and best.min_detectable_tokens else "the configured minimum"
    text = PLAIN_TEXT[band].format(
        rarity=one_in_n(best.p_value_adjusted) if best else "",
        min_tokens=min_tokens,
    )
    return {
        "result_id": str(uuid.uuid4()),
        "request_id": None,
        "requested_at": now,
        "requested_by": requested_by,
        "text_digest": digest,
        "token_count": token_count,
        "scored_token_count": scored,
        "fpr_target": fpr_target,
        "band": band.value,
        "band_reason": f"{reason}. {text}",
        "best_match": best.config_id if best else None,
        "candidates": [
            {
                "config_id": r.config_id, "scheme_id": r.scheme_id, "key_version": r.key_version,
                "statistic": r.statistic, "p_value": r.p_value, "p_value_adjusted": r.p_value_adjusted,
                "ci_95": list(r.ci_95) if r.ci_95 else None,
                "min_detectable_tokens": r.min_detectable_tokens, "null_model_ref": r.null_model_ref,
            }
            for r in results
        ],
        "caveats": caveats,
        "engine": {"markwright_version": __version__, "mode": mode, "adapter_versions": {}},
        "signature": None,
    }
