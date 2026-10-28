"""The verification pipeline: gate -> candidates -> detect -> calibrate -> correct -> band.

Scheme-agnostic. The only scheme-specific call is ``adapter.detect``.
"""
from __future__ import annotations

import hashlib
import hmac
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from markwright import __version__
from markwright.schemes.base import Detection, Detector, KeyHandle, SchemeAdapter, TokenIds
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


def _digest(text: str | TokenIds, salt: bytes | None) -> str | None:
    if salt is None or not isinstance(text, str):
        return None   # token-ids submissions carry no normalisable text to digest
    norm = " ".join(text.split()).encode()
    return hmac.new(salt, norm, hashlib.sha256).hexdigest()


def verify(
    text: str | TokenIds,
    candidates: list[Candidate],
    fpr_target: float,
    *,
    count_tokens: Callable[[str, str], int],
    deployment_salt: bytes | None = None,
    requested_by: str | None = None,
    mode: str = "server",
) -> Result:
    """Gate → candidates → detect → calibrate → Bonferroni → band.

    ``text`` may be raw text or pre-tokenized ids (design §5/§8.1). Token
    counting is per candidate tokenizer (configs may disagree on tokenizers);
    a candidate whose tokenizer is unavailable, or whose flags carry
    ``tokenizer_uncertain`` (hash mismatch, set by the caller), is never
    scored — no silent fallback (design §7.1 step 1, POC plan phase 3)."""
    now = datetime.now(UTC).isoformat()
    caveats: set[str] = {ALWAYS_CAVEAT}
    results: list[CandidateResult] = []
    scored_max = 0
    is_ids = not isinstance(text, str)

    if not candidates:
        return Result(_payload(now, 0, 0, fpr_target, Band.INSUFFICIENT, "no candidate configurations",
                               None, [], sorted(caveats), requested_by, _digest(text, deployment_salt), mode))

    k = len(candidates)
    if k > 1:
        caveats.add("multiple_candidates")

    counts: dict[str, int | None] = {}   # tokenizer_ref -> count, None = tokenizer unavailable
    token_count = len(text) if is_ids else 0
    floor: float | None = None
    for c in candidates:
        if "tokenizer_uncertain" in c.flags:          # hash mismatch detected by the caller
            caveats.add("tokenizer_uncertain")
            continue
        if is_ids:
            tc = len(text)
        else:
            if c.tokenizer_ref not in counts:
                try:
                    counts[c.tokenizer_ref] = count_tokens(text, c.tokenizer_ref)
                except Exception:
                    counts[c.tokenizer_ref] = None    # unavailable ≠ zero: never guess a count
            tc = counts[c.tokenizer_ref]
            if tc is None:
                caveats.add("tokenizer_uncertain")
                continue
            token_count = max(token_count, tc)
        if tc < c.min_tokens:
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
        if c.null_model.quality != "standard":
            caveats.add("provisional_calibration")
        floor = max(floor or 0.0, c.null_model.resolution_floor())

    gate_ok = bool(results)
    best = min(results, key=lambda r: r.p_value_adjusted) if results else None
    band, reason = band_for(best.p_value_adjusted if best else None, fpr_target, gate_ok,
                            resolution_floor=floor)
    if not gate_ok and "tokenizer_uncertain" in caveats and "short_text" not in caveats:
        reason = "tokenizer unavailable or mismatched; result would be unreliable"
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
