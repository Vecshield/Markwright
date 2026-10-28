"""Tier-2: the REAL SynthID adapter on checked-in token-id fixtures.

Deterministic and offline: g-values are table lookups from ids + derived keys —
no model weights, no tokenizer, no network. Fixtures regenerate with
tests/fixtures/regenerate.py. Tests that do need a tokenizer are marked
``network`` and excluded from CI tiers.
"""
from __future__ import annotations

import importlib.util

import pytest

if importlib.util.find_spec("torch") is None:          # tier-1 environments
    pytest.skip("torch not installed (tier-2 suite)", allow_module_level=True)

pytestmark = pytest.mark.torch

from markwright.schemes.base import KeyHandle
from markwright.schemes.synthid import SynthIDTextAdapter

from .conftest import FIXTURE_KEY_V1, FIXTURE_KEY_V2, load_token_ids

A = SynthIDTextAdapter()
P = A.default_params()


@pytest.mark.parametrize("case", ["marked_120", "marked_250", "marked_450"])
def test_marked_fixtures_score_far_above_half_with_right_key(case):
    ids = load_token_ids(case)
    d = A.detect(ids, P, FIXTURE_KEY_V1, "gpt2")
    assert d.statistic_name == "mean_g"
    assert d.statistic_value > 0.72
    assert d.scored_tokens >= len(ids) - 10            # repetition mask trims little here
    assert d.analytic_p_value is not None and d.analytic_p_value < 1e-8
    assert "eos_mask_skipped_token_ids_input" in d.notes


@pytest.mark.parametrize("case", ["marked_120", "marked_250", "marked_450"])
def test_wrong_key_sees_no_signal(case):
    """The test that makes key custody mean something: v2 key on v1-marked text
    sees mean_g ≈ 0.5 while the right key sees ≈ 0.8.

    Deliberately NOT asserted on p-values: the analytic p overstates slight
    deviations on long sequences (independence assumption — the reason the
    design forbids reporting it), and the calibrated p of these fixtures sits
    at the null-model's floor because uniform-logit sequences are out of
    domain for a fiction-fitted null (the exact domain-shift limitation
    data/null/README.md documents). The custody claim is the statistic gap."""
    ids = load_token_ids(case)
    d_wrong = A.detect(ids, P, FIXTURE_KEY_V2, "gpt2")
    d_right = A.detect(ids, P, FIXTURE_KEY_V1, "gpt2")
    assert abs(d_wrong.statistic_value - 0.5) < 0.06
    assert d_right.statistic_value - d_wrong.statistic_value > 0.2


@pytest.mark.parametrize("case", ["unmarked_100_0", "unmarked_200_0", "unmarked_400_0",
                                  "unmarked_200_1", "unmarked_400_2"])
def test_unmarked_corpus_passages_score_near_half(case):
    d = A.detect(load_token_ids(case), P, FIXTURE_KEY_V1, "gpt2")
    assert abs(d.statistic_value - 0.5) < 0.08


def test_truncation_degrades_scored_tokens_not_the_statistic():
    ids = load_token_ids("marked_450")
    prev_scored = None
    for keep in (456, 300, 150, 80):
        d = A.detect(ids[:keep], P, FIXTURE_KEY_V1, "gpt2")
        assert d.statistic_value > 0.7                 # the mark is per-token; mean stays high
        if prev_scored is not None:
            assert d.scored_tokens < prev_scored       # evidence mass shrinks monotonically
        prev_scored = d.scored_tokens


def test_below_ngram_window_returns_gated_zero():
    d = A.detect(load_token_ids("marked_120")[:4], P, FIXTURE_KEY_V1, "gpt2")
    assert d.scored_tokens == 0 and "too_short_for_ngram" in d.notes


def test_export_detector_roundtrip_matches_key_detection():
    ids = load_token_ids("marked_250")
    det = A.export_detector(P, FIXTURE_KEY_V1)
    assert det is not None and det.kind == "artifact" and det.sha256
    via_key = A.detect(ids, P, FIXTURE_KEY_V1, "gpt2")
    via_detector = A.detect(ids, P, det, "gpt2")
    assert via_detector.statistic_value == pytest.approx(via_key.statistic_value, abs=1e-9)
    assert via_detector.scored_tokens == via_key.scored_tokens


def test_derived_keys_are_deterministic_and_version_scoped():
    k1a = A._keys(P, FIXTURE_KEY_V1)
    k1b = A._keys(P, FIXTURE_KEY_V1)
    k2 = A._keys(P, FIXTURE_KEY_V2)
    assert k1a == k1b and k1a != k2 and len(k1a) == P["num_keys"]
    sealed_less = KeyHandle("k", 1, "local-sealed", "r", sealed_material=None)
    with pytest.raises(PermissionError):
        A._keys(P, sealed_less)


def test_params_schema_roundtrip_and_hf_config():
    import jsonschema

    jsonschema.validate(P, A.params_schema())
    cfg = A.hf_watermarking_config(P, FIXTURE_KEY_V1)
    assert len(cfg.keys) == 30 and cfg.ngram_len == 5


@pytest.mark.network
def test_text_and_ids_paths_agree():
    """Same input through the tokenizer path and the ids path → same statistic
    (modulo EOS masking, absent in this text). Needs the gpt2 tokenizer."""
    from transformers import AutoTokenizer

    text = ("The committee reviewed the proposal on Tuesday and asked for further data on "
            "regional water usage. A revised draft, incorporating comments from the public "
            "consultation, will be circulated before the next session in March. Several "
            "members questioned the cost estimates for the northern pipeline extension.") * 2
    tok = AutoTokenizer.from_pretrained("gpt2")
    ids = tok(text, add_special_tokens=False)["input_ids"]
    d_text = A.detect(text, P, FIXTURE_KEY_V1, "gpt2")
    d_ids = A.detect(ids, P, FIXTURE_KEY_V1, "gpt2")
    assert d_text.statistic_value == pytest.approx(d_ids.statistic_value, abs=1e-9)
    assert abs(d_text.statistic_value - 0.5) < 0.1     # unmarked prose ≈ 0.5
