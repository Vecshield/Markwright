"""Inference hook, Hugging Face `generate` flavour.

Fetches the generation profile from Markwright, builds the SynthID watermarking
config, and generates. Prompts and completions never reach Markwright.

    MARKWRIGHT_URL=http://localhost:8000 MARKWRIGHT_TOKEN=operator-token \\
    python hooks/hf_generate_example.py --model-id demo-llm --model-version 1.0 \\
        --model HuggingFaceTB/SmolLM2-135M-Instruct --prompt "..." [--config <id>]

One API call: GET .../generation-profile returns scheme id, params, tokenizer
ref and the derived key material (MVP: hex over the local API; v1 seals it to
an ephemeral X25519 session key — same response shape, so this hook will not
change). The vLLM plugin (v1) does the same but returns the LogitsProcessor
from ``adapter.apply(config)`` into vLLM's ``logits_processors`` list.
"""
from __future__ import annotations

import argparse
import os

import httpx
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from markwright.schemes import get
from markwright.schemes.base import KeyHandle


def fetch_profile(model_id: str, model_version: str, config_id: str | None = None) -> dict:
    api = httpx.Client(base_url=os.environ.get("MARKWRIGHT_URL", "http://localhost:8000"),
                       headers={"Authorization": f"Bearer {os.environ.get('MARKWRIGHT_TOKEN', 'operator-token')}"})
    r = api.get(f"/v1/models/{model_id}/versions/{model_version}/generation-profile",
                params={"config_id": config_id} if config_id else None)
    r.raise_for_status()
    return r.json()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-id", required=True, help="Markwright model id (registry slug)")
    ap.add_argument("--model-version", required=True)
    ap.add_argument("--config", default=None, help="explicit config id (e.g. a draft, for test generation)")
    ap.add_argument("--model", required=True, help="HF id or local path of the actual weights")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--max-new-tokens", type=int, default=400)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    prof = fetch_profile(args.model_id, args.model_version, args.config)
    key = KeyHandle(prof["key_ref"], prof["key_version"], prof["backend"], prof["backend_ref"],
                    bytes.fromhex(prof["sealed_material"]))
    adapter = get(prof["scheme_id"])
    wm_cfg = adapter.hf_watermarking_config(prof["scheme_params"], key)

    if args.seed is not None:
        torch.manual_seed(args.seed)
    tok = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForCausalLM.from_pretrained(args.model, torch_dtype="auto")
    inputs = tok(args.prompt, return_tensors="pt")
    s = prof["scheme_params"].get("sampling", {})
    out = model.generate(**inputs, do_sample=True, temperature=s.get("temperature", 1.0),
                         top_k=s.get("top_k", 40), top_p=s.get("top_p", 0.99),
                         max_new_tokens=args.max_new_tokens, watermarking_config=wm_cfg)
    print(tok.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True))


if __name__ == "__main__":
    main()
