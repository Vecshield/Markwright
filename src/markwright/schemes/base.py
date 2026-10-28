"""The uniform scheme-adapter contract.

Every watermarking scheme is exposed to the core through this Protocol. The
core treats ``scheme_params`` as opaque JSON validated by ``params_schema()``
and never imports scheme math directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol, Sequence, runtime_checkable

# Design §5: detect() accepts raw text OR pre-tokenized ids. The ids path needs
# no tokenizer (and, for SynthID, no model at all), which is what makes offline
# verification, deterministic tier-2 tests and tokenizer-free calibration possible.
TokenIds = Sequence[int]


@dataclass(frozen=True)
class Capabilities:
    distortion_free: bool = False          # sampling distribution unchanged (Kuditipudi, Aaronson, CGZ)
    needs_model_for_detect: bool = False   # detection requires model logits (rare; heavy)
    publishable_detector: bool = False     # export_detector() can return a stranger-runnable artifact
    backend_mac_supported: bool = False    # PRF schemes: detection can run with the key inside the KMS
    multi_key_scan: bool = True            # detect() can be run per key version cheaply
    detect_cost: str = "low"               # "low" | "medium" | "high" (permutation tests)
    experimental: bool = False


@dataclass(frozen=True)
class KeyHandle:
    """Reference to key material held by a KeyBackend. Never contains raw bytes
    unless the backend has explicitly sealed them for this process."""
    key_id: str
    key_version: int
    backend: str
    backend_ref: str
    sealed_material: bytes | None = None   # present only on an inference host, short TTL


@dataclass
class GenerationConfig:
    """Serialisable description the inference hook turns into a logits processor."""
    scheme_id: str
    scheme_version: str
    params: dict[str, Any]
    key: KeyHandle
    tokenizer_ref: str


@dataclass
class Detection:
    statistic_name: str
    statistic_value: float
    scored_tokens: int
    analytic_p_value: float | None = None       # adapter's closed-form value, if it has one
    min_detectable_tokens: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass
class Detector:
    """Publishable artifact for key-less verification (SynthID Bayesian detector),
    or a descriptor of a detect-only service for PRF schemes."""
    kind: str                       # "artifact" | "service"
    scheme_id: str
    payload: dict[str, Any]
    sha256: str


@dataclass
class CalibrationReport:
    config_id: str
    null_corpus_ids: list[str]
    null_model: dict[str, Any]              # serialised NullModel per length bucket
    min_detectable_tokens: int | None
    robustness: dict[str, Any]              # {attack: {edit_fraction: tpr_at_target}}
    fpr_target: float
    notes: list[str] = field(default_factory=list)


@runtime_checkable
class SchemeAdapter(Protocol):
    scheme_id: str
    scheme_version: str
    capabilities: Capabilities

    def params_schema(self) -> dict: ...
    def default_params(self) -> dict: ...

    def generate_config(self, params: dict, key: KeyHandle, tokenizer_ref: str) -> GenerationConfig: ...

    def apply(self, config: GenerationConfig) -> Any:
        """Return an in-process logits processor (HF LogitsProcessor or compatible callable)."""
        ...

    def detect(self, text: str | TokenIds, params: dict, key: KeyHandle | Detector, tokenizer_ref: str) -> Detection: ...

    def calibrate(
        self,
        params: dict,
        key: KeyHandle,
        tokenizer_ref: str,
        null_corpus: Iterable[str | TokenIds],
        marked_corpus: Iterable[str | TokenIds] | None,
        edit_fractions: list[float],
        fpr_target: float,
    ) -> CalibrationReport: ...

    def export_detector(self, params: dict, key: KeyHandle) -> Detector | None: ...
