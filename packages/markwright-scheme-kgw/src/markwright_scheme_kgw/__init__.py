"""KGW green-list watermark adapter (Kirchenbauer et al. 2023).

Wraps transformers' ``WatermarkingConfig`` / ``WatermarkLogitsProcessor`` /
``WatermarkDetector``. Markwright adds only: HKDF derivation of the 64-bit
hashing key from the backend-held root, serialisation, and the adapter
contract. No green-list or z-test math lives here.
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from markwright.keys.derive import derive_seed
from markwright.schemes.base import (
    CalibrationReport,
    Capabilities,
    Detection,
    Detector,
    GenerationConfig,
    KeyHandle,
    TokenIds,
)

SCHEME_ID = "kgw"

PARAMS_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "KGWParams",
    "type": "object",
    "additionalProperties": False,
    "required": ["gamma", "delta", "vocab_size"],
    "properties": {
        "gamma": {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1, "default": 0.25,
                  "description": "green-list fraction of the vocabulary"},
        "delta": {"type": "number", "exclusiveMinimum": 0, "default": 2.0,
                  "description": "logit bias added to green tokens; quality cost grows with it"},
        "seeding_scheme": {"type": "string", "enum": ["lefthash", "selfhash"], "default": "lefthash"},
        "context_width": {"type": "integer", "minimum": 1, "maximum": 8, "default": 1},
        "vocab_size": {"type": "integer", "minimum": 2,
                       "description": "green lists are per-vocabulary; MUST match the tokenizer"},
        "bos_token_id": {"type": ["integer", "null"], "default": None,
                         "description": "stripped by the detector if leading; default vocab_size-1 (= gpt2 eos/bos)"},
        "ignore_repeated_ngrams": {"type": "boolean", "default": True},
        "z_threshold": {"type": "number", "default": 4.0,
                        "description": "advisory only; Markwright bands come from the calibrated null model"},
        "min_tokens": {"type": "integer", "minimum": 1, "default": 50},
    },
}


def _require_transformers():
    try:
        import torch  # noqa: F401
        from transformers import (  # noqa: F401
            WatermarkDetector,
            WatermarkingConfig,
            WatermarkLogitsProcessor,
        )
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "kgw adapter needs `pip install markwright-scheme-kgw[torch]` (torch + transformers>=4.46)"
        ) from e


class KGWAdapter:
    scheme_id = SCHEME_ID
    scheme_version = "0.1.0"
    capabilities = Capabilities(
        distortion_free=False,          # the bias shifts the sampling distribution
        needs_model_for_detect=False,
        publishable_detector=False,     # the green list IS the key (design §5 table)
        backend_mac_supported=False,
        multi_key_scan=True,
        detect_cost="low",
    )

    # ---- contract -----------------------------------------------------------------
    def params_schema(self) -> dict:
        return PARAMS_SCHEMA

    def default_params(self) -> dict:
        return {
            "gamma": 0.25,
            "delta": 2.0,
            "seeding_scheme": "lefthash",
            "context_width": 1,
            "vocab_size": 50257,            # gpt2; override per model
            "bos_token_id": None,
            "ignore_repeated_ngrams": True,
            "z_threshold": 4.0,
            "min_tokens": 50,
        }

    def generate_config(self, params: dict, key: KeyHandle, tokenizer_ref: str) -> GenerationConfig:
        return GenerationConfig(self.scheme_id, self.scheme_version, params, key, tokenizer_ref)

    def _hashing_key(self, params: dict, key: KeyHandle) -> int:
        if key.sealed_material is None:
            raise PermissionError("key material not unsealed for this process")
        info = f"markwright/{SCHEME_ID}/{key.key_id}/v{key.key_version}"
        # transformers expects a plain int; 63 bits keeps it positive everywhere
        return derive_seed(key.sealed_material, info) & 0x7FFF_FFFF_FFFF_FFFF

    def hf_watermarking_config(self, params: dict, key: KeyHandle):
        """Pass as ``watermarking_config=`` to HF ``generate``."""
        _require_transformers()
        from transformers import WatermarkingConfig

        return WatermarkingConfig(
            greenlist_ratio=params["gamma"],
            bias=params["delta"],
            hashing_key=self._hashing_key(params, key),
            seeding_scheme=params["seeding_scheme"],
            context_width=params.get("context_width", 1),
        )

    def apply(self, config: GenerationConfig):
        """HF-style LogitsProcessor for vLLM / custom samplers."""
        _require_transformers()
        from transformers import WatermarkLogitsProcessor

        p = config.params
        return WatermarkLogitsProcessor(
            vocab_size=p["vocab_size"],
            device="cpu",
            greenlist_ratio=p["gamma"],
            bias=p["delta"],
            hashing_key=self._hashing_key(p, config.key),
            seeding_scheme=p["seeding_scheme"],
            context_width=p.get("context_width", 1),
        )

    def detect(self, text: str | TokenIds, params: dict, key: KeyHandle | Detector,
               tokenizer_ref: str) -> Detection:
        _require_transformers()
        import torch
        from transformers import PretrainedConfig, WatermarkDetector

        if isinstance(key, Detector):
            # Capabilities said so: the green list is derived from the seed, so
            # there is no key-free artifact. Refuse loudly, never approximate.
            raise ValueError("kgw has no publishable detector; detection requires the key "
                             "(use a detect-only service or a verification-key lineage, design §6.4)")

        notes: list[str] = []
        if isinstance(text, str):
            from transformers import AutoTokenizer

            tok = AutoTokenizer.from_pretrained(tokenizer_ref)
            ids = tok(text, return_tensors="pt", add_special_tokens=False)["input_ids"]
        else:
            ids = torch.tensor([list(text)], dtype=torch.long)
        n_tokens = int(ids.shape[1])
        needed = params.get("context_width", 1) + 1
        if n_tokens <= needed:
            return Detection("kgw_z", 0.0, 0, notes=[*notes, "too_short_for_context"])

        model_config = PretrainedConfig(
            vocab_size=params["vocab_size"],
            bos_token_id=params.get("bos_token_id") if params.get("bos_token_id") is not None
            else params["vocab_size"] - 1,
            is_encoder_decoder=False,
        )
        detector = WatermarkDetector(
            model_config, "cpu", self.hf_watermarking_config(params, key),
            ignore_repeated_ngrams=params.get("ignore_repeated_ngrams", True))
        out = detector(ids, return_dict=True)

        z = float(out.z_score[0])
        scored = int(out.num_tokens_scored[0])
        if scored == 0:
            return Detection("kgw_z", 0.0, 0, notes=[*notes, "no_scorable_tokens"])
        if not params.get("ignore_repeated_ngrams", True):
            notes.append("repeated_ngrams_counted")   # design: repeated n-grams deflate T honestly
        # NOTE: the HF output also carries `prediction` (a boolean) and
        # `confidence`. Markwright's first honesty rule is NO booleans — they
        # are deliberately not propagated into the Detection.
        analytic_p = 0.5 * math.erfc(z / math.sqrt(2))
        return Detection(
            "kgw_z", z, scored,
            analytic_p_value=analytic_p,
            extra={"green_fraction": float(out.green_fraction[0]),
                   "num_green": int(out.num_green_tokens[0]),
                   "key_version": key.key_version},
            notes=notes,
        )

    def calibrate(
        self,
        params: dict,
        key: KeyHandle,
        tokenizer_ref: str,
        null_corpus: Iterable[str | TokenIds],
        marked_corpus: Iterable[str | TokenIds] | None,
        edit_fractions: list[float],
        fpr_target: float,
    ) -> CalibrationReport:
        from markwright.stats.calibrate import fit_null_by_length, min_detectable_length

        null_stats = [self.detect(t, params, key, tokenizer_ref) for t in null_corpus]
        null_model = fit_null_by_length(null_stats)
        mdl = None
        if marked_corpus is not None:
            marked_stats = [self.detect(t, params, key, tokenizer_ref) for t in marked_corpus]
            mdl = min_detectable_length(marked_stats, null_model, fpr_target)
        return CalibrationReport(
            config_id="", null_corpus_ids=[], null_model=null_model,
            min_detectable_tokens=mdl, robustness={}, fpr_target=fpr_target,
            notes=["the analytic p-value assumes tokens are independent, which real text violates; trust the calibrated p-value"],
        )

    def export_detector(self, params: dict, key: KeyHandle) -> Detector | None:
        return None   # the seed IS the key; nothing separable to publish
