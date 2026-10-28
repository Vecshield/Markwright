# CLAUDE.md — Markwright

This file is the working contract for Claude Code sessions and human contributors
in **this** repository. It overrides any `CLAUDE.md` found in parent directories
(the one in `~/Downloads/` belongs to an unrelated ESM-2 protein project; its
Conda/Make/SAFE_MODE rules do not apply here).

## What Markwright is

An Apache-2.0 **control plane for LLM text watermarking**: a versioned registry
binding model versions to published watermarking schemes, key custody behind a
`KeyBackend` boundary, and calibrated probabilistic verification. It wraps
published schemes (SynthID-Text via HF transformers; later KGW/Unigram/Kuditipudi
via MarkLLM) and **invents no algorithm and no cryptography**.

Authoritative design: `LLM Watermark Management System — Design.pdf`
(39 pages, lives outside the repo in ~/Downloads/). Key sections: §5 adapter
contract, §6 key management, §7 verification/statistics, §8 API/CLI/UI map,
§11 phased roadmap, §12 scaffold. When code and design conflict, record the
discrepancy in `docs/IMPLEMENTATION_LOG.md` and resolve explicitly — never
silently pick one.

## Non-negotiable product rules (enforced in code, keep them enforced)

1. **No booleans.** No API/SDK field may answer "is this AI?". `Result.__bool__`
   raises. Results are a band + calibrated p-value + FPR target + caveats.
2. **The gate precedes the statistic.** Short / low-entropy text returns
   `insufficient_evidence` with no p-value, ever.
3. **Honest calibration.** A null model that cannot resolve the deployment's
   FPR target must cap the band and carry the `provisional_calibration` caveat
   (`NullModel.resolution_floor()`); never report precision the calibration
   cannot support.
4. **Tokenizer integrity.** No silent tokenizer fallback. A missing or
   mismatched tokenizer yields `tokenizer_uncertain` + `insufficient_evidence`.
5. **Never store submitted text.** Salted digest + statistics only. Audit
   entries reject `text`/`prompt`/`secret` keys.
6. **Keys never leave the backend boundary** except as short-lived sealed
   material to an inference hook. The DB stores handles, not secrets.
7. Bonferroni over scanned candidates; `no_evidence` always states it is not
   evidence of human authorship.

## Repository structure

```
markwright/
├── CLAUDE.md                  # this contract
├── pyproject.toml             # extras: synthid, markllm, vault, aws, gcp, azure, postgres, dev
├── docker-compose.yml         # profiles: dev | onprem | provider
├── schemas/                   # JSON Schema 2020-12 — the API/CLI/SDK wire contract.
│                              #   Loaded by tests; responses MUST validate against these.
├── src/markwright/
│   ├── schemes/               # base.py = SchemeAdapter Protocol (§5); synthid.py = only real adapter.
│   │                          #   New schemes are NEW PACKAGES via entry point `markwright.schemes`,
│   │                          #   never core edits.
│   ├── keys/                  # KeyBackend Protocol, HKDF derivation, local sealed vault (AES-GCM+scrypt)
│   ├── stats/                 # null model (ECDF+GPD tail), bands, calibration fitting. Pure; no I/O.
│   ├── verify/                # scheme-agnostic pipeline: gate→candidates→detect→calibrate→Bonferroni→band
│   ├── registry/              # SQLite store: models, versions, immutable configs, keys, calibrations, results
│   ├── calibration/           # corpus loading + calibration run orchestration
│   ├── audit/                 # hash-chained, Ed25519-anchored audit log
│   ├── api/                   # FastAPI — the single surface; every other surface is a client of it
│   ├── cli/                   # typer CLI (thin API client + offline core imports)
│   └── jobs/                  # background jobs (v1: arq/APScheduler; currently stub)
├── data/null/                 # bundled starter null corpora (small, provisional-grade; see README there)
├── hooks/                     # inference-side examples (HF generate; vLLM plugin in v1)
├── tests/                     # three tiers — see "Testing" below
│   └── fixtures/              # recorded real detections + token-id fixtures; regenerate.py rebuilds them
├── dashboard/                 # React+TS (Vite) — NOT YET BUILT (Milestone B phase 6)
├── deploy/                    # Dockerfile, compose assets, Helm chart skeleton
└── docs/
    ├── poc-plan.md            # the plan being executed (milestones A/B, phases 0–7)
    ├── IMPLEMENTATION_LOG.md  # append-only session log — READ THIS FIRST when resuming
    ├── quickstart.md          # 15-minute laptop walkthrough
    └── limitations.md         # responsible-use text shipped in the product UI
```

## Agentic development organization

How work is organized so that any session (human or agent) can pick up where the
last one stopped:

- **Plan**: `docs/poc-plan.md` holds the phased plan (Milestone A = working POC,
  Milestone B = infra parity). Work proceeds phase by phase. Do not invent new
  phases silently — amend the plan file in the same commit.
- **Log**: `docs/IMPLEMENTATION_LOG.md` is append-only. After completing a step,
  append: what changed, why, how verified, what was deliberately deferred.
  A session that ends mid-phase MUST log exactly where it stopped.
- **Resume procedure**: read `CLAUDE.md` → `docs/IMPLEMENTATION_LOG.md` (last
  entry) → `git log --oneline -15` → run tier-1 tests → continue the first
  unchecked item in the current phase of `docs/poc-plan.md`.
- **Commits**: one commit per phase (or coherent sub-step), message prefixed
  with the phase id, e.g. `phase-1: calibration path end to end`. Baseline
  scaffold is commit `fe9eeed`; everything since is reviewable diffs.
- **Tests travel with code**: a phase is not done until its tier-1 tests pass
  locally. Never commit with tier-1 red. Red tier-2 (torch) must be logged.
- **Design-doc discrepancies** found while coding go in the log under a
  "Discrepancies" note in the step entry, with the chosen resolution.

## Environment

- Python ≥ 3.11 (dev machine: `python3.12` via `uv`). System python3 is 3.9 —
  do not use it.
- Setup: `uv venv .venv --python 3.12 && uv pip install --python .venv/bin/python -e ".[dev,synthid]"`
- Run things through `.venv/bin/python` / `.venv/bin/pytest`. No Conda here.
- Torch/transformers are required only for the `synthid` extra and tier-2/3
  tests; the core, API, stats and tier-1 tests run without them.

## Testing (three tiers)

| Tier | Marker | Needs | When |
|---|---|---|---|
| 1 | (none) | core deps only, <10 s | every change; blocks commit |
| 2 | `torch` | torch+transformers, **no network, no model weights** — token-id fixtures drive the real SynthID adapter deterministically | every change when torch installed; blocks merge |
| 3 | `slow` | network + small model download (SmolLM2-135M) | nightly / release; regenerates `tests/fixtures/` via `tests/fixtures/regenerate.py` |

- `.venv/bin/pytest -q` runs tiers 1+2 (tier 2 auto-skips without torch);
  `-m "not torch"` forces tier 1 only; `-m slow` opts into tier 3.
- Fixtures are **regenerable artifacts, not magic numbers**: never hand-edit
  JSON under `tests/fixtures/`; change `regenerate.py` and rerun it.
- Mock adapters are allowed only as `ReplayAdapter` replaying *recorded real*
  detections; a stub returning an invented constant is not acceptable in new code.

## Coding rules

- Typed Python, `from __future__ import annotations`.
- Keep `stats/` and `verify/` pure (no I/O); I/O lives at module boundaries
  (api, cli, registry, calibration).
- Schemas in `schemas/` are the wire contract: change the schema and the code
  in the same commit; tier-1 tests validate responses against them.
- No pickle for artifacts. JSON/YAML for human-reviewed things.
- Atomic writes for anything durable (tmp + rename — see `local_sealed.py`).
- Standard crypto primitives from `cryptography` only; never hand-rolled.
- Statistics via numpy/scipy standard estimators; no bespoke math.

## Current status (keep this section fresh)

- Milestone A (working POC) — implemented in this repo: phases 0–3 plus test
  tiers 1–2. See the log for exact state and the demo script.
- Milestone B (infra parity: key-lifecycle API, audit persistence/signing,
  artifact store, detector publish, batch verify, offline bundles, 2-screen
  dashboard) — **not started**; specified in `docs/poc-plan.md`.
- Dashboard is absent; `docker compose --profile dev up` will fail on the
  dashboard service until Milestone B phase 6.
