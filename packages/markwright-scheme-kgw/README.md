# markwright-scheme-kgw

Kirchenbauer et al. green-list (KGW) watermark adapter for
[Markwright](../../README.md), wrapping the Hugging Face transformers
implementation (`WatermarkingConfig` / `WatermarkLogitsProcessor` /
`WatermarkDetector`). This package adds only key derivation (HKDF → 64-bit
seed), serialisation and the Markwright adapter contract — no sampling or
detection math lives here.

This package exists partly as the **architecture test** from the design doc
(§12): adding a scheme is a new package registering the
`markwright.schemes` entry point, never a core change.

Properties the registry should know (surfaced via `capabilities`):

- **Not distortion-free** — the bias measurably shifts the sampling
  distribution (quality cost grows with `delta`).
- **No publishable detector** — the green list is derived from the seed, so
  detection discloses the key. External verification requires a detect-only
  service or a verification-key lineage (design §6.4); `export_detector()`
  returns `None` and Markwright's detector-publish route answers 409.

Install (editable, alongside the main repo):

```bash
uv pip install --python .venv/bin/python -e "packages/markwright-scheme-kgw"
```
