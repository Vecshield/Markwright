# Implementation log

Append-only record of what was done, in order, so any human or agent can resume
mid-stream. Newest entry last. Each entry: what changed, why, how it was verified,
and what is deliberately left undone. The plan being executed is
[docs/poc-plan.md](poc-plan.md); conventions are in [CLAUDE.md](../CLAUDE.md).

Resume procedure: read CLAUDE.md, then this file top to bottom, then `git log
--oneline`. The last entry tells you where work stopped.

---

## 2026-10-28 · Session 1 (Claude Code)

### Step 0.0 — Baseline commit
- `git init -b main`, `.gitignore`, committed the 44-file design-doc scaffold
  as-received (`fe9eeed`), so every subsequent change is reviewable as a diff.
- Known-broken at baseline (verified by static read, recorded here so nobody
  re-discovers it): no calibration path → no config can activate → `/v1/verify`
  always `insufficient_evidence`; `generation-profile` endpoint missing but
  called by `hooks/hf_generate_example.py`; `dashboard/` empty but referenced by
  all docker-compose profiles; `tests/test_pipeline_and_api.py` asserts the
  deadlock as correct; `_count_tokens` silently falls back to `str.split()`.

### Step 0.1 — Toolchain
- Environment: macOS arm64, system Python 3.9 (too old), but `python3.12` and
  `uv 0.12` available, network OK.
- `.venv` created with `uv venv --python 3.12`; deps installed with
  `uv pip install -e ".[dev,synthid]"` (torch + transformers included so the
  real-adapter test tier can run).
- NOTE for future sessions: the `CLAUDE.md` one directory up
  (`~/Downloads/CLAUDE.md`) belongs to an unrelated ESM-2 project and forbids
  pip/venv. It does not govern this repo; this repo's own `CLAUDE.md` does.

(further entries appended below as steps complete)

### Step 0.2–0.5 — Project scaffolding (commit: phase-0)
- `LICENSE`: full Apache-2.0 text fetched from apache.org (was a 2-line stub).
- `.github/workflows/ci.yml`: tier-1 (3.11/3.12, ruff + pytest w/o torch) and
  tier-2 (torch, offline fixtures) jobs via uv.
- `pyproject.toml`: pytest markers `torch`/`slow`/`network`; wheel force-includes
  `schemas/` → `markwright/resources/schemas` and `data/null/` →
  `markwright/resources/null_corpora` (design §4: one wire contract everywhere).
- `src/markwright/jobs/worker.py`: explicit stub with exit message (compose
  `worker` service referenced a module that didn't exist).
- `CLAUDE.md` (repo-local contract), `docs/poc-plan.md` (the plan),
  this log. Plan checkboxes are kept truthful per commit.
- Verified: `.venv/bin/pytest -q` → **14 passed, 1 failed** at baseline.
  The failure (`test_detect_on_unmarked_text_is_near_half`) is the TEST's fault,
  not the adapter: its text repeats one sentence 6×, SynthID's context-repetition
  mask correctly scores only the first occurrence (29 tokens < asserted 50).
  statistic=0.51≈0.5 confirms transformers 5.21 API-compatible with the adapter.
  Fix lands in phase 7 with varied-text fixtures.
- Discrepancy noted: installed transformers is 5.21 (scaffold assumed >=4.46);
  `SynthIDTextWatermarkLogitsProcessor` signature unchanged → no code change.
