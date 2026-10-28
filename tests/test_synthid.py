"""Exercises the real SynthID adapter against transformers. Skipped when torch is absent."""
import pytest

torch = pytest.importorskip("torch")
transformers = pytest.importorskip("transformers")

from markwright.schemes.base import KeyHandle
from markwright.schemes.synthid import SynthIDTextAdapter


def test_params_schema_and_hf_config_roundtrip():
    a = SynthIDTextAdapter()
    import jsonschema
    jsonschema.validate(a.default_params(), a.params_schema())
    key = KeyHandle("k", 1, "local-sealed", "ref", sealed_material=b"\x01" * 32)
    cfg = a.hf_watermarking_config(a.default_params(), key)
    assert len(cfg.keys) == 30 and cfg.ngram_len == 5


def test_detect_on_unmarked_text_is_near_half():
    a = SynthIDTextAdapter()
    key = KeyHandle("k", 1, "local-sealed", "ref", sealed_material=b"\x02" * 32)
    text = ("The committee met on Tuesday to review the proposal and agreed to postpone the decision "
            "until further data on regional water usage had been collected and analysed. ") * 6
    d = a.detect(text, a.default_params(), key, "gpt2")
    assert d.statistic_name == "mean_g" and d.scored_tokens > 50
    assert 0.35 < d.statistic_value < 0.65
    assert d.analytic_p_value is not None and 0 <= d.analytic_p_value <= 1
