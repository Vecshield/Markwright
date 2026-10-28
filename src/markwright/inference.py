"""Inference-hook helpers: the only Markwright code that runs inside an
operator's serving stack (design §3). One API call fetches the generation
profile; everything else happens in-process so prompts and completions never
reach Markwright.

Two integration shapes:

- **HF generate**: ``hf_watermarking_config`` from the profile's adapter —
  see hooks/hf_generate_example.py.
- **vLLM**: :class:`VLLMLogitsProcessor` wraps the adapter's HF-style
  processor (batch tensors) into vLLM's per-request logits-processor calling
  convention. Interface-level support: exercised against a simulated vLLM
  sampling loop in tests/test_vllm_plugin.py; running under a real vLLM
  deployment needs a Linux/CUDA host (vLLM does not install on macOS) and is
  tracked in the implementation log as unverified-live.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from markwright.schemes import get as get_scheme
from markwright.schemes.base import KeyHandle


def fetch_generation_profile(model_id: str, model_version: str, config_id: str | None = None,
                             base_url: str | None = None, token: str | None = None) -> dict[str, Any]:
    api = httpx.Client(
        base_url=base_url or os.environ.get("MARKWRIGHT_URL", "http://localhost:8000"),
        headers={"Authorization": f"Bearer {token or os.environ.get('MARKWRIGHT_TOKEN', 'operator-token')}"})
    r = api.get(f"/v1/models/{model_id}/versions/{model_version}/generation-profile",
                params={"config_id": config_id} if config_id else None)
    r.raise_for_status()
    return r.json()


def key_handle_from_profile(prof: dict[str, Any]) -> KeyHandle:
    return KeyHandle(prof["key_ref"], prof["key_version"], prof["backend"], prof["backend_ref"],
                     bytes.fromhex(prof["sealed_material"]))


def build_hf_processor(prof: dict[str, Any]):
    """The adapter's HF-style LogitsProcessor (batch ids, batch scores)."""
    adapter = get_scheme(prof["scheme_id"])
    cfg = adapter.generate_config(prof["scheme_params"], key_handle_from_profile(prof),
                                  prof["tokenizer_ref"])
    return adapter.apply(cfg)


class VLLMLogitsProcessor:
    """Adapts an HF-style processor to vLLM's per-request convention.

    vLLM calls logits processors per sequence with either
    ``(token_ids, logits)`` or ``(prompt_token_ids, generated_token_ids,
    logits)`` where ``logits`` is a 1-D vocab tensor; HF processors want
    ``(input_ids [1, T], scores [1, V])``. Stateless adaptation: concatenate,
    unsqueeze, call, squeeze.

        prof = fetch_generation_profile("acme-llm", "4.1")
        sampling_params = SamplingParams(..., logits_processors=[VLLMLogitsProcessor(prof)])
    """

    def __init__(self, prof: dict[str, Any]):
        self._hf = build_hf_processor(prof)
        self.config_id = prof["config_id"]
        self.scheme_id = prof["scheme_id"]

    def __call__(self, *args):
        import torch

        if len(args) == 2:                       # (token_ids, logits)
            token_ids, logits = args
        elif len(args) == 3:                     # (prompt_token_ids, generated_token_ids, logits)
            prompt_ids, gen_ids, logits = args
            token_ids = list(prompt_ids) + list(gen_ids)
        else:  # pragma: no cover - defensive
            raise TypeError(f"unexpected vLLM logits-processor arity: {len(args)}")
        if not token_ids:
            return logits                        # no context yet -> nothing to seed on
        ids = torch.tensor([list(token_ids)], dtype=torch.long, device=logits.device)
        return self._hf(ids, logits.unsqueeze(0)).squeeze(0)
