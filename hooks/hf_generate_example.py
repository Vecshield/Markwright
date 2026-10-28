"""Inference hook, Hugging Face `generate` flavour.

Fetches the generation profile from Markwright, builds the SynthID watermarking
config, and generates. Prompts and completions never reach Markwright.

    MARKWRIGHT_URL=http://localhost:8000 MARKWRIGHT_TOKEN=... \
    python hooks/hf_generate_example.py --config <config_id> --model google/gemma-2-2b-it --prompt "..."

The vLLM plugin (v1) does the same but returns the LogitsProcessor from
``adapter.apply(config)`` into vLLM's ``logits_processors`` list.
"""
from __future__ import annotations

import argparse
import os

import httpx
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from markwright.schemes import get
from markwright.schemes.base import KeyHandle


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--prompt", required=True)
    args = ap.parse_args()

    api = httpx.Client(base_url=os.environ["MARKWRIGHT_URL"],
                       headers={"Authorization": f"Bearer {os.environ['MARKWRIGHT_TOKEN']}"})
    cfg = api.get(f"/v1/configs/{args.config}").json()
    # MVP: the workload derives material through the generation-profile endpoint (v1 seals it to an
    # ephemeral session key). Here we call the same endpoint shape.
    prof = api.get(f"/v1/models/{cfg['model_id']}/versions/{cfg['model_version']}/generation-profile",
                   params={"config_id": args.config}).json()
    key = KeyHandle(cfg["key_ref"], prof["key_version"], prof["backend"], prof["backend_ref"],
                    bytes.fromhex(prof["sealed_material"]))

    adapter = get(cfg["scheme_id"])
    wm_cfg = adapter.hf_watermarking_config(cfg["scheme_params"], key)

    tok = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForCausalLM.from_pretrained(args.model, torch_dtype=torch.bfloat16)
    inputs = tok(args.prompt, return_tensors="pt")
    s = cfg["scheme_params"].get("sampling", {})
    out = model.generate(**inputs, do_sample=True, temperature=s.get("temperature", 1.0), top_k=s.get("top_k", 40),
                         top_p=s.get("top_p", 0.99), max_new_tokens=400, watermarking_config=wm_cfg)
    print(tok.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True))


if __name__ == "__main__":
    main()
