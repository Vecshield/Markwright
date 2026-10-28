"""Tier-2: the vLLM logits-processor wrapper, against a SIMULATED vLLM
sampling loop (per-request ids list + 1-D vocab logits — the exact calling
convention). Real-vLLM execution needs Linux/CUDA and is tracked as
unverified-live in the implementation log."""
from __future__ import annotations

import importlib.util

import pytest

if importlib.util.find_spec("torch") is None:
    pytest.skip("torch not installed (tier-2 suite)", allow_module_level=True)

pytestmark = pytest.mark.torch

import torch

from markwright.inference import VLLMLogitsProcessor
from markwright.schemes.synthid import SynthIDTextAdapter

from .conftest import FIXTURE_KEY_V1

VOCAB = 50_000


def _profile() -> dict:
    """A generation-profile payload as the API returns it (fixture key)."""
    a = SynthIDTextAdapter()
    return {
        "config_id": "test-config",
        "scheme_id": "synthid-text",
        "scheme_version": a.scheme_version,
        "scheme_params": a.default_params(),
        "tokenizer_ref": "gpt2",
        "key_ref": FIXTURE_KEY_V1.key_id,
        "key_version": FIXTURE_KEY_V1.key_version,
        "backend": FIXTURE_KEY_V1.backend,
        "backend_ref": FIXTURE_KEY_V1.backend_ref,
        "sealed_material": FIXTURE_KEY_V1.sealed_material.hex(),
    }


def _simulate_vllm(proc: VLLMLogitsProcessor, n: int, arity: int, seed: int = 0) -> list[int]:
    """Drive the wrapper exactly as vLLM does: one sequence, 1-D logits."""
    torch.manual_seed(seed)
    prompt = torch.randint(100, VOCAB, (5,)).tolist()
    generated: list[int] = []
    for _ in range(n):
        logits = torch.zeros(VOCAB)                    # uniform base logits (no LM)
        if arity == 2:
            out = proc(prompt + generated, logits)
        else:
            out = proc(prompt, generated, logits)
        assert out.shape == (VOCAB,)                   # vLLM contract: 1-D in, 1-D out
        nxt = int(torch.multinomial(torch.softmax(out / 0.7, -1), 1))
        generated.append(nxt)
    return prompt + generated


@pytest.mark.parametrize("arity", [2, 3])
def test_vllm_convention_produces_detectable_marks(arity):
    proc = VLLMLogitsProcessor(_profile())
    assert proc.scheme_id == "synthid-text"
    seq = _simulate_vllm(proc, 220, arity)
    a = SynthIDTextAdapter()
    d = a.detect(seq, a.default_params(), FIXTURE_KEY_V1, "gpt2")
    assert d.statistic_value > 0.72 and d.scored_tokens > 180


def test_vllm_wrapper_matches_hf_batch_processor_exactly():
    """Same context → identical biased logits through both conventions."""
    prof = _profile()
    proc = VLLMLogitsProcessor(prof)
    from markwright.inference import build_hf_processor
    hf = build_hf_processor(prof)
    ctx = list(range(100, 120))
    logits = torch.zeros(VOCAB)
    via_vllm = proc(ctx, logits.clone())
    via_hf = hf(torch.tensor([ctx]), logits.clone().unsqueeze(0)).squeeze(0)
    assert torch.equal(via_vllm, via_hf)


def test_empty_context_passes_logits_through():
    proc = VLLMLogitsProcessor(_profile())
    logits = torch.randn(VOCAB)
    assert torch.equal(proc([], logits.clone()), logits)