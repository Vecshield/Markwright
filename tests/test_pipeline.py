"""Pipeline-level honesty rules, driven by RECORDED REAL SynthID detections
(ReplayAdapter) against the REAL fitted null model fixture. No torch needed;
no invented constants (CLAUDE.md testing rules)."""
from __future__ import annotations

import pytest

from markwright.verify import Candidate, verify

from .conftest import FIXTURE_KEY_V1, ReplayAdapter, load_token_ids

MARKED = load_token_ids("marked_250")
UNMARKED = load_token_ids("unmarked_200_0")


def _cand(real_null_model, cid="c1", min_tokens=50, flags=(), key=FIXTURE_KEY_V1):
    return Candidate(cid, "synthid-text", ReplayAdapter(), {}, key, "gpt2",
                     real_null_model, min_tokens, 180, key.key_version, list(flags))


def _no_tokenizer(text, ref):   # ids-path tests must never count via a tokenizer
    raise AssertionError("count_tokens must not be called for token-ids input")


def test_marked_fixture_yields_calibrated_band_with_caveats(real_null_model):
    r = verify(MARKED, [_cand(real_null_model)], 1e-2, count_tokens=_no_tokenizer)
    assert r["band"] == "moderate_evidence"          # floor ~3.4e-3 sits between 1e-4 and 1e-2
    c = r["candidates"][0]
    assert c["statistic"]["value"] > 0.75            # recorded real mean_g
    lo, hi = c["ci_95"]
    assert 0 < lo <= c["p_value_adjusted"] * 1.5 and lo <= hi <= 1
    assert {"provisional_calibration", "possible_paraphrase", "result_is_probabilistic"} <= set(r["caveats"])


def test_unmarked_fixture_is_no_evidence_but_keeps_its_pvalue(real_null_model):
    r = verify(UNMARKED, [_cand(real_null_model)], 1e-2, count_tokens=_no_tokenizer)
    assert r["band"] == "no_evidence" and r["best_match"] is None
    # §7.3: no_evidence is still a measurement — the candidate row keeps its p-value
    assert len(r["candidates"]) == 1 and r["candidates"][0]["p_value"] > 0.05
    assert "not evidence of human authorship" in r["band_reason"] or \
           "does not mean the text is human-written" in r["band_reason"]


def test_fpr_finer_than_resolution_floor_caps_the_band(real_null_model):
    floor = real_null_model.resolution_floor()
    r = verify(MARKED, [_cand(real_null_model)], fpr_target=floor / 30, count_tokens=_no_tokenizer)
    assert r["band"] == "weak_evidence"
    assert "calibration resolves p down to" in r["band_reason"]


def test_short_input_gates_before_any_statistic(real_null_model):
    r = verify(UNMARKED[:30], [_cand(real_null_model)], 1e-2, count_tokens=_no_tokenizer)
    assert r["band"] == "insufficient_evidence" and r["candidates"] == []
    assert "short_text" in r["caveats"]


def test_bonferroni_over_candidates(real_null_model):
    cands = [_cand(real_null_model, f"c{i}") for i in range(3)]
    cands[2].flags.append("spoofing_not_excluded")
    r = verify(MARKED, cands, 1e-2, count_tokens=_no_tokenizer)
    for c in r["candidates"]:
        assert abs(c["p_value_adjusted"] - min(1.0, 3 * c["p_value"])) < 1e-12
    assert {"multiple_candidates", "spoofing_not_excluded"} <= set(r["caveats"])


def test_tokenizer_uncertain_flag_skips_candidate(real_null_model):
    r = verify(MARKED, [_cand(real_null_model, flags=("tokenizer_uncertain",))], 1e-2,
               count_tokens=_no_tokenizer)
    assert r["band"] == "insufficient_evidence" and "tokenizer_uncertain" in r["caveats"]
    assert "tokenizer" in r["band_reason"]


def test_count_tokens_failure_is_uncertain_never_a_guess(real_null_model):
    def broken(text, ref):
        raise RuntimeError("no such tokenizer")
    r = verify("some text " * 50, [_cand(real_null_model)], 1e-2, count_tokens=broken)
    assert r["band"] == "insufficient_evidence" and "tokenizer_uncertain" in r["caveats"]


def test_digest_only_for_text_input(real_null_model):
    r_ids = verify(MARKED, [_cand(real_null_model)], 1e-2,
                   count_tokens=_no_tokenizer, deployment_salt=b"s")
    assert r_ids["text_digest"] is None
    # text path: gates on short_text (count 30 < min 50) but the digest is still
    # recorded — digesting is independent of whether any candidate was scored
    r_txt = verify("hello " * 60, [_cand(real_null_model)], 1e-2,
                   count_tokens=lambda t, _: 30, deployment_salt=b"s")
    assert isinstance(r_txt["text_digest"], str) and len(r_txt["text_digest"]) == 64


def test_result_refuses_boolean_coercion(real_null_model):
    r = verify(MARKED, [_cand(real_null_model)], 1e-2, count_tokens=_no_tokenizer)
    with pytest.raises(TypeError):
        bool(r)
