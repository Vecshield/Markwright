"""Inference hook, vLLM flavour (design §3: the hook is the only component
outside Markwright; it fetches the generation profile and builds the logits
processor in-process — prompts and completions never reach Markwright).

    MARKWRIGHT_URL=http://localhost:8000 MARKWRIGHT_TOKEN=operator-token \\
    python hooks/vllm_example.py --model-id demo-llm --model-version 1.0 \\
        --model HuggingFaceTB/SmolLM2-135M-Instruct --prompt "..."

Needs a vLLM-capable host (Linux/CUDA; vLLM does not install on macOS). The
wrapper itself (markwright.inference.VLLMLogitsProcessor) is covered by a
simulated-sampler test in tests/test_vllm_plugin.py.
"""
from __future__ import annotations

import argparse

from markwright.inference import VLLMLogitsProcessor, fetch_generation_profile


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-id", required=True, help="Markwright registry slug")
    ap.add_argument("--model-version", required=True)
    ap.add_argument("--config", default=None)
    ap.add_argument("--model", required=True, help="HF id or local path of the weights")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--max-new-tokens", type=int, default=400)
    args = ap.parse_args()

    try:
        from vllm import LLM, SamplingParams
    except ImportError:
        raise SystemExit("vLLM is not installed (Linux/CUDA only). "
                         "Use hooks/hf_generate_example.py for the laptop loop.") from None

    prof = fetch_generation_profile(args.model_id, args.model_version, args.config)
    s = prof["scheme_params"].get("sampling", {})
    llm = LLM(model=args.model)
    out = llm.generate(
        [args.prompt],
        SamplingParams(
            temperature=s.get("temperature", 1.0),
            top_k=s.get("top_k", 40),
            top_p=s.get("top_p", 0.99),
            max_tokens=args.max_new_tokens,
            logits_processors=[VLLMLogitsProcessor(prof)],
        ),
    )
    print(out[0].outputs[0].text)


if __name__ == "__main__":
    main()
