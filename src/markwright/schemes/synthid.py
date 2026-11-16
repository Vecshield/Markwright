"""SynthID-Text adapter.

Wraps the Hugging Face transformers implementation (>= 4.46):
``SynthIDTextWatermarkingConfig``, ``SynthIDTextWatermarkLogitsProcessor``,
``SynthIDTextWatermarkDetector`` and ``BayesianDetectorModel``.

Markwright adds only: key derivation from a backend-held root secret,
serialisation, a length/entropy gate and calibration hooks. No sampling or
detection math lives here.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from typing import Any

from markwright.keys.derive import derive_int_keys
from markwright.schemes.base import (
    CalibrationReport,
    Capabilities,
    Detection,
    Detector,
    GenerationConfig,
    KeyHandle,
    TokenIds,
)

SCHEME_ID = "synthid-text"

PARAMS_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "SynthIDTextParams",
    "type": "object",
    "additionalProperties": False,
    "required": ["ngram_len", "num_keys"],
    "properties": {
        "ngram_len": {"type": "integer", "minimum": 2, "maximum": 10, "default": 5},
        "num_keys": {"type": "integer", "minimum": 20, "maximum": 30, "default": 30,
                      "description": "Tournament depth; keys are derived, not stored"},
        "context_history_size": {"type": "integer", "minimum": 1, "default": 1024},
        "sampling_table_size": {"type": "integer", "minimum": 2, "default": 65536},
        "sampling_table_seed": {"type": "integer", "default": 0},
        "skip_first_ngram_calls": {"type": "boolean", "default": False},
        "sampling": {
            "type": "object",
            "description": "Recorded for reproducibility; applied by the host sampler",
            "properties": {
                "temperature": {"type": "number", "exclusiveMinimum": 0, "default": 1.0},
                "top_k": {"type": "integer", "minimum": 1, "default": 40},
                "top_p": {"type": "number", "exclusiveMinimum": 0, "maximum": 1, "default": 0.99},
            },
        },
        "detector": {"type": "string", "enum": ["mean", "bayesian"], "default": "mean"},
        "min_tokens": {"type": "integer", "minimum": 1, "default": 50},
    },
}


def _require_transformers():
    try:
        import torch  # noqa: F401
        from transformers import (  # noqa: F401
            SynthIDTextWatermarkDetector,
            SynthIDTextWatermarkingConfig,
            SynthIDTextWatermarkLogitsProcessor,
        )
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "SynthID-Text adapter needs `pip install markwright[synthid]` (torch + transformers>=4.46)"
        ) from e


class SynthIDTextAdapter:
    scheme_id = SCHEME_ID
    scheme_version = "0.1.0"
    capabilities = Capabilities(
        distortion_free=False,
        needs_model_for_detect=False,
        publishable_detector=True,     # Bayesian detector + key list
        backend_mac_supported=False,
        multi_key_scan=True,
        detect_cost="low",
    )

    # ---- contract -----------------------------------------------------------------
    def params_schema(self) -> dict:
        return PARAMS_SCHEMA

    def default_params(self) -> dict:
        return {
            "ngram_len": 5,
            "num_keys": 30,
            "context_history_size": 1024,
            "sampling_table_size": 65536,
            "sampling_table_seed": 0,
            "skip_first_ngram_calls": False,
            "sampling": {"temperature": 1.0, "top_k": 40, "top_p": 0.99},
            "detector": "mean",
            "min_tokens": 50,
        }

    def generate_config(self, params: dict, key: KeyHandle, tokenizer_ref: str) -> GenerationConfig:
        return GenerationConfig(
            scheme_id=self.scheme_id,
            scheme_version=self.scheme_version,
            params=params,
            key=key,
            tokenizer_ref=tokenizer_ref,
        )

    def _keys(self, params: dict, key: KeyHandle) -> list[int]:
        if key.sealed_material is None:
            raise PermissionError("key material not unsealed for this process")
        info = f"markwright/{SCHEME_ID}/{key.key_id}/v{key.key_version}"
        return derive_int_keys(key.sealed_material, info, params["num_keys"])

    def hf_watermarking_config(self, params: dict, key: KeyHandle):
        """The object to pass as ``generation_config.watermarking_config`` to HF ``generate``."""
        _require_transformers()
        from transformers import SynthIDTextWatermarkingConfig

        return SynthIDTextWatermarkingConfig(
            keys=self._keys(params, key),
            ngram_len=params["ngram_len"],
            context_history_size=params.get("context_history_size", 1024),
            sampling_table_size=params.get("sampling_table_size", 65536),
            sampling_table_seed=params.get("sampling_table_seed", 0),
            skip_first_ngram_calls=params.get("skip_first_ngram_calls", False),
        )

    def apply(self, config: GenerationConfig):
        """Build the HF LogitsProcessor directly (for vLLM / custom samplers)."""
        _require_transformers()
        import torch
        from transformers import SynthIDTextWatermarkLogitsProcessor

        p = config.params
        return SynthIDTextWatermarkLogitsProcessor(
            ngram_len=p["ngram_len"],
            keys=self._keys(p, config.key),
            sampling_table_size=p.get("sampling_table_size", 65536),
            sampling_table_seed=p.get("sampling_table_seed", 0),
            context_history_size=p.get("context_history_size", 1024),
            device=torch.device("cpu"),
            skip_first_ngram_calls=p.get("skip_first_ngram_calls", False),
        )

    def detect(self, text: str | TokenIds, params: dict, key: KeyHandle | Detector, tokenizer_ref: str) -> Detection:
        _require_transformers()
        import torch

        notes: list[str] = []
        if isinstance(text, str):
            # Text path: the tokenizer is REQUIRED (design §5: a detector run with
            # the wrong tokenizer produces confident nonsense; no silent fallback).
            from transformers import AutoTokenizer

            tok = AutoTokenizer.from_pretrained(tokenizer_ref)
            ids = tok(text, return_tensors="pt", add_special_tokens=False)["input_ids"]
            eos_token_id = tok.eos_token_id
        else:
            # Token-ids path (design §5 `TokenIds`): no tokenizer, no network, no
            # model — g-values are pure table lookups from ids + keys. Used by
            # offline verification, calibration over pre-tokenized corpora, and
            # the deterministic tier-2 tests. EOS masking is skipped (pre-tokenized
            # corpora carry no EOS); noted on the Detection for transparency.
            ids = torch.tensor([list(text)], dtype=torch.long)
            eos_token_id = None
            notes.append("eos_mask_skipped_token_ids_input")
        n_tokens = int(ids.shape[1])

        if isinstance(key, Detector):
            key_list = key.payload["keys"]
            handle = KeyHandle("published", key.payload["key_version"], "detector", key.sha256)
            processor = self._processor_from_keys(params, key_list)
        else:
            handle = key
            processor = self.apply(self.generate_config(params, key, tokenizer_ref))

        if n_tokens < params["ngram_len"] + 1:
            return Detection("mean_g", 0.5, 0, notes=[*notes, "too_short_for_ngram"])

        g = processor.compute_g_values(ids)                           # [1, T-ngram+1, depth]
        mask = processor.compute_context_repetition_mask(ids)         # [1, T-ngram+1]
        if eos_token_id is not None:
            eos = processor.compute_eos_token_mask(ids, eos_token_id)[:, params["ngram_len"] - 1:]
            mask = mask * eos
        mask = mask.bool()
        scored = int(mask.sum())
        if scored == 0:
            return Detection("mean_g", 0.5, 0, notes=[*notes, "no_scorable_tokens"])

        per_token = g.float().mean(dim=-1)[mask]                      # mean over depths per token
        mean_g = float(per_token.mean())
        # Normal approximation on per-token means (independence assumed; the core
        # ALWAYS re-maps this statistic through the calibrated null model).
        sd = float(per_token.std(unbiased=True)) if scored > 1 else 0.5
        z = (mean_g - 0.5) / (sd / math.sqrt(scored)) if sd > 0 else 0.0
        analytic_p = 0.5 * math.erfc(z / math.sqrt(2))

        extra: dict[str, Any] = {"z": z, "analytic_p": analytic_p, "key_version": handle.key_version}

        if params.get("detector") == "bayesian" and isinstance(key, Detector) and "bayesian_weights" in key.payload:
            from transformers import BayesianDetectorModel

            model = BayesianDetectorModel.from_pretrained(key.payload["bayesian_weights"])
            with torch.no_grad():
                out = model(g_values=g.float(), mask=mask.float())
            post = float(out.posterior_probabilities[0])
            extra["bayesian_posterior"] = post
            return Detection("bayesian_posterior", post, scored, analytic_p_value=None, extra=extra, notes=notes)

        return Detection("mean_g", mean_g, scored, analytic_p_value=analytic_p, extra=extra, notes=notes)

    def _processor_from_keys(self, params: dict, keys: list[int]):
        import torch
        from transformers import SynthIDTextWatermarkLogitsProcessor

        return SynthIDTextWatermarkLogitsProcessor(
            ngram_len=params["ngram_len"],
            keys=keys,
            sampling_table_size=params.get("sampling_table_size", 65536),
            sampling_table_seed=params.get("sampling_table_seed", 0),
            context_history_size=params.get("context_history_size", 1024),
            device=torch.device("cpu"),
            skip_first_ngram_calls=params.get("skip_first_ngram_calls", False),
        )

    def calibrate(
        self,
        params: dict,
        key: KeyHandle,
        tokenizer_ref: str,
        null_corpus: Iterable[str],
        marked_corpus: Iterable[str] | None,
        edit_fractions: list[float],
        fpr_target: float,
    ) -> CalibrationReport:
        """Run detect() over the null corpus and hand the statistics to the generic
        calibrator (markwright.stats). Scheme-specific only in what statistic it feeds."""
        from markwright.stats.calibrate import fit_null_by_length, min_detectable_length

        null_stats = [self.detect(t, params, key, tokenizer_ref) for t in null_corpus]
        null_model = fit_null_by_length(null_stats)
        mdl = None
        robustness: dict[str, Any] = {}
        if marked_corpus is not None:
            marked_stats = [self.detect(t, params, key, tokenizer_ref) for t in marked_corpus]
            mdl = min_detectable_length(marked_stats, null_model, fpr_target)
            # Edit-fraction sweeps reuse MarkLLM's text-editing attacks when installed;
            # left to the calibration job in v1.
        return CalibrationReport(
            config_id="", null_corpus_ids=[], null_model=null_model,
            min_detectable_tokens=mdl, robustness=robustness, fpr_target=fpr_target,
            notes=["the analytic p-value assumes tokens are independent, which real text violates; trust the calibrated p-value"],
        )

    def export_detector(self, params: dict, key: KeyHandle) -> Detector | None:
        """SynthID detection needs the key list to recompute g-values, so a published
        detector discloses the keys. Only do this on a lineage labelled public-detector."""
        payload = {
            "scheme_id": SCHEME_ID,
            "params": params,
            "keys": self._keys(params, key),
            "key_version": key.key_version,
            # "bayesian_weights": "<artifact uri>"  # set by the calibration job when trained
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        return Detector(kind="artifact", scheme_id=SCHEME_ID, payload=payload, sha256=digest)
