# Markwright

**An open-source control plane for LLM text watermarking.** Registry of models ↔ schemes ↔ parameters,
key custody with rotation, and calibrated, honest verification. Apache-2.0.

Markwright does **not** implement a watermarking algorithm and is **not** an "AI detector". It wires up
published schemes (SynthID-Text via Hugging Face transformers; KGW, Unigram, Kuditipudi EXP/ITS via MarkLLM;
Aaronson and Christ–Gunn–Zamir PRF schemes) and operates them: who marked what with which key, and how
strong the statistical evidence is that a text carries a specific mark.

## Quickstart (laptop, no cloud)

```bash
uv venv .venv --python 3.12 && uv pip install --python .venv/bin/python -e ".[synthid,dev]"
.venv/bin/uvicorn markwright.api.app:app     # API on :8000 (dashboard: Milestone B)
export MARKWRIGHT_TOKEN=operator-token       # dev tokens; v1 uses OIDC

markwright init                              # 5 steps: model -> scheme -> keys -> test generation -> verify
markwright verify --file essay.txt

./scripts/demo.sh                            # or: the full loop, self-checking, in ~30 s
```

See [docs/quickstart.md](docs/quickstart.md) for the step-by-step version, and
[docs/poc-plan.md](docs/poc-plan.md) + [docs/IMPLEMENTATION_LOG.md](docs/IMPLEMENTATION_LOG.md)
for what is implemented vs planned (the Compose dev profile still references the
not-yet-built dashboard).

A result looks like this, and never like "AI: yes":

```
Result: MODERATE EVIDENCE
  calibrated p-value    2.1e-05  (95% interval 9.0e-06 - 4.8e-05), adjusted for 6 config(s) scanned
  false-positive target 1.0e-04  (deployment policy)
  tokens                412 total, 398 scored  (minimum detectable for this config: 180)
  caveats:
    - result is probabilistic, not proof
    - paraphrase or editing can remove marks; a positive may be partial
```

## What is in this repository

| Path | Purpose |
| --- | --- |
| `src/markwright/schemes/` | The adapter contract (`base.py`) and the SynthID-Text adapter |
| `src/markwright/keys/` | `KeyBackend` protocol, HKDF derivation, local sealed vault (air-gapped / dev) |
| `src/markwright/stats/` | Null model (ECDF + GPD tail), bands, calibration helpers |
| `src/markwright/verify/` | The scheme-agnostic verification pipeline |
| `src/markwright/registry/` | Model / version / config registry (SQLite now, Postgres in v1) |
| `src/markwright/audit/` | Hash-chained, Ed25519-anchored audit log |
| `src/markwright/api/` | FastAPI app, the single surface everything else uses |
| `src/markwright/cli/` | `markwright` CLI |
| `schemas/` | JSON Schemas for config, key record, verification result, audit entry |
| `hooks/` | Inference-side examples (HF `generate`; vLLM plugin in v1) |
| `deploy/` | Dockerfiles, Compose assets, Helm chart |
| `docs/` | Quickstart, operator guide, **limitations and responsible use** |

## Honesty rules (enforced in code)

- No boolean anywhere in a result; `bool(result)` raises.
- Short or low-entropy text returns `insufficient_evidence`, with no p-value.
- Every result carries the false-positive target, the token count against the minimum detectable length,
  and caveats; `no_evidence` always says it is not evidence of human authorship.
- Scanning *k* configurations Bonferroni-corrects the p-value.
- Submitted text is never stored; only a salted digest and the statistics.
- Audit entries that contain text, prompts or secrets are rejected.

## Status

0.1 (MVP): registry + SynthID-Text + local keys + calibrated verify, end to end. See `docs/roadmap.md`.

## Licence

Apache-2.0. Contributions under the DCO. Security issues: see `SECURITY.md`.
