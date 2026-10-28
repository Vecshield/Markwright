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

### Phase 1 (+2.1) — Pipeline unblocked end to end (commit: phase-1)
What changed, in dependency order:
- **1.1** `schemes/base.py`: `TokenIds` alias; `detect(text: str | TokenIds)`.
  `schemes/synthid.py`: ids path builds the tensor directly — no tokenizer, no
  network, no model (g-values are table lookups). EOS masking skipped on ids
  input, recorded in `Detection.notes`. Text path now REQUIRES the tokenizer.
- **1.2** `scripts/build_null_corpus.py` (network, manual) built
  `data/null/gutenberg-en-v1.jsonl`: 900 passages from 4 public-domain novels,
  pre-tokenized gpt2 ids, 300 each near 100/200/400 tokens, 1.0 MB.
  `data/null/README.md` documents provenance + why it is provisional-grade.
- **1.3** `stats/null_model.py`: `NullBucket.floor`, `NullModel.resolution_floor()`
  (max across buckets — conservative), `quality` tag, explicit
  `TAIL_EXTRAPOLATION=1e-3` (was a magic clamp), per-bucket precomputed
  bootstrap re-fits (`fit_bucket(n_boot=...)`) with explicit to_dict/from_dict.
  `stats/bands.py`: `band_for(..., resolution_floor=)` refuses STRONG/MODERATE
  when fpr_target < floor, with a plain-language reason.
  `verify/pipeline.py`: accepts token ids; per-candidate token counting with
  a cache; count failure → `tokenizer_uncertain`, never a guess; floor passed
  to band_for; `provisional_calibration` caveat; `tokenizer_uncertain` flag
  from the caller skips the candidate. Schema caveats enum updated.
- **1.4** `registry/store.py`: `calibrations` table + save/get/list/accept +
  `accepted_null_models()` (startup hydration). accept also records the
  tokenizer hash into the config's verification_defaults (3.1 support).
  New `calibration/` package: `corpus.py` (bundled JSONL discovery; corpus↔
  tokenizer compatibility map), `run.py` (synchronous run, tokenizer_hash()).
  API: `POST /v1/configs/{id}/calibrations`, `GET /v1/calibrations/{run}`,
  `GET /v1/configs/{id}/calibrations`, `POST /v1/calibrations/{run}:accept`,
  `GET /v1/null-corpora`; null models hydrate from the DB at import.
- **1.5** CLI `markwright calibrate run|show|accept` with a readable panel.
- **1.6** provider profile refuses activating provisional calibrations (409);
  activation audit entry records calibration quality.
- **1.7** policy fpr_target: env `MARKWRIGHT_FPR_TARGET`, default 1e-2 in dev
  profile / 1e-4 otherwise, because the starter corpus floor is ~3.4e-3.
- **2.1** `GET /v1/models/{id}/versions/{v}/generation-profile` (operator;
  returns scheme params + derived material hex; audited `key.handle_issue`;
  refuses deprecated/retired configs; `?config_id=` selects drafts for test
  generation). MVP form — X25519 session sealing is v1; response shape final.
- Fixtures: `tests/fixtures/regenerate.py` — marked sequences generated by
  driving the SynthID logits processor over uniform logits (NO model needed;
  mean_g≈0.80 right key / ≈0.51 wrong key), unmarked from the corpus; recorded
  real Detections for the tier-1 ReplayAdapter. 12 token-id cases, 24 detections.

Bugs found & fixed:
- LATENT BASELINE BUG: `NullModel.p_value` interpolation did `max()/min()` over
  `NullBucket` objects without a key → TypeError with ≥3 buckets (never hit
  before because nothing ever fit 3 buckets). Fixed with explicit keys.

Verified (in-process API, full loop): calibrate 900 docs in 8.4s → accept →
activate OK; marked ids (via generation-profile key, processor-driven)
→ `moderate_evidence` p_adj=3.4e-3 with `provisional_calibration` caveat;
unmarked → `no_evidence`; fpr tightened to 1e-4 → capped `weak_evidence` with
floor explanation; 30-token input → `insufficient_evidence`; verify latency
~0.02s (precomputed bootstrap; was going to be seconds).

Deliberately left undone: marked-corpus calibration (min_detectable_tokens
stays null, reported in notes); X25519 sealing; async calibration jobs.

### Phases 2.2–2.3, 3, 7 + exit gate (commit: phase-2-3-7)
- **2.2** `hooks/hf_generate_example.py` rewritten against the real
  generation-profile response (one API call; `--max-new-tokens`, `--seed`).
- **2.3** CLI `markwright generate test --config --prompt --model` (friendly
  error without the synthid extra).
- **3.1/3.2** were implemented alongside phase 1 (same files): tokenizer hash
  recorded at calibration-accept, compared at verify; `_count_tokens` raises
  instead of word-splitting; per-candidate counting in the pipeline.
- **7.x** Test suite converted from mocks to real:
  - deleted `test_pipeline_and_api.py` (its StubAdapter returned an invented
    constant, and its last two assertions enshrined the calibration deadlock).
  - `tests/conftest.py`: `ReplayAdapter` (replays recorded REAL SynthID
    detections), `ParityAdapter` (a real torch-free miniature scheme:
    statistic = mean token parity) + `bootstrap_parity_config` driving every
    step over real HTTP routes, fresh-state `api` fixture.
  - `tests/test_pipeline.py` (tier 1): honesty rules against the REAL fitted
    null-model fixture — bands, floor capping, Bonferroni, tokenizer-uncertain,
    digest rules, bool-raises.
  - `tests/test_api.py` (tier 1): full loop incl. restart hydration; lifecycle
    state machine incl. no-resurrection; RBAC matrix (11 cases); calibration
    route validation (unknown corpus, tokenizer mismatch); generation-profile
    (issue+audit, 403 for verifier, 409 for deprecated); every verify response
    and audit entry validated against `schemas/*.json`.
  - `tests/test_synthid.py` (tier 2, offline): real adapter over token-id
    fixtures — marked>0.72 / unmarked≈0.5 / WRONG KEY sees no signal (asserted
    on the statistic gap, NOT p-values: analytic p overstates on long
    sequences, and calibrated p of uniform-logit fixtures sits at the floor —
    the domain-shift limitation documented in data/null/README.md, now also in
    the test's docstring); truncation monotonicity; export_detector roundtrip;
    HKDF determinism/version scoping. Str-vs-ids consistency test marked
    `network` (needs the gpt2 tokenizer).
  - `tests/test_stats.py`: + resolution-floor semantics, band capping, quality
    tag, precomputed-boot interval + serialisation roundtrip, and a regression
    test for the 3-bucket interpolation TypeError.
  - fixtures also include `null_model.synthid.gutenberg-en-v1.json` — a real
    calibration output used by tier-1.
- Lint: explicit ruff ruleset in pyproject (so CI and local agree); B904/E702
  fixes; `Band` → `StrEnum`.
- README + docs/quickstart.md updated to the real commands (uv, uvicorn,
  gutenberg-en-v1, generate test, demo.sh); note that Compose dev still
  references the unbuilt dashboard.
- **Milestone A exit gate PASSED** against a live uvicorn server
  (`./scripts/demo.sh` → scripts/demo.py): register → key → calibrate(prov.)
  → activate → processor-driven marked generation via generation-profile →
  verify marked = moderate_evidence (p_adj 3.4e-3, provisional caveat) /
  human = no_evidence / 30 tokens = insufficient (no p-value) → audit chain
  verifies with all 7 expected action types present.
- Suite: 65 passed (tier1+2), 1 network-marked; ruff clean.

### Where this leaves the repo
Milestone A complete. Next session starts at Milestone B phase 4.1 (key
lifecycle routes — vault rotate/revoke/destroy exist and are uncalled) or
phase 6 (dashboard) depending on priority. docs/poc-plan.md is current.

## 2026-10-28 · Session 2 (Claude Code) — Milestone B

### Phase 4 + 5 — key lifecycle, signed audit, detectors, batch, offline (commit: phase-4-5)
- **4.3 first (foundational)**: `AuditChain` gains sqlite persistence (table
  `audit_log` in the registry DB; reloads on construction → restarts keep the
  chain). Deployment Ed25519 identity: a `purpose=signing` key record whose
  seed is DERIVED inside the vault boundary (`markwright/deployment-signing/v1`),
  never stored. `export_bundle`/`verify_bundle`: tar.gz of entries + manifest +
  Ed25519 signature + pubkey, verifiable fully offline; wrong-deployment keys
  and tampered entries are detected (tests prove both).
  Routes: `POST /v1/audit/export`, `GET /v1/audit/public-key`.
  CLI: `markwright audit tail|export|verify [--pubkey]`.
- **4.1/4.2 key lifecycle**: `POST /v1/keys`, `GET /v1/keys[/{id}]?lineage=`,
  `:rotate` (old key → verify-only + generate_until, verifies forever; each
  active config is deprecated and superseded by a NEW active config version on
  the new key, inheriting the accepted calibration — the null distribution
  under human text is key-independent; decision logged here), `:revoke`
  (admin; marking stops, verification continues flagged historical_key, reason
  recorded), `:destroy` (admin; refused until verify_until passes — null means
  verify forever). Purpose separation enforced: `_key_handle` refuses
  non-generation keys; configs cannot reference the signing key; the
  generation-profile refuses non-active keys and picks the newest active
  config after rotation. CLI `key create|show|list|rotate|revoke`.
- **4.4** `markwright/artifacts.py`: `ArtifactStore` Protocol + atomic,
  content-hash-checked `LocalArtifactStore` (`local://` URIs; S3 is v1).
- **4.5** `POST /v1/configs/{id}/detector:publish` → artifact store, sets
  `labels.disclosure=public-detector` (→ spoofing_not_excluded on every later
  result), 409 for schemes that cannot separate detection from the key;
  `GET /v1/detectors/{id}` is readable by any authenticated role (it is public
  by definition). CLI `detector publish [--out]`.
- **4.6** `POST /v1/verify/batch` (≤1000, synchronous for POC — async worker is
  v1; summary reports observed positives NEXT TO n×fpr expected-by-chance per
  design §7.4), `GET /v1/verify/results` (role-scoped: verifier/operator own,
  auditor/admin all; metadata only), `PUT /v1/policy` (admin, audited
  policy.change), `GET /readyz`, `GET /metrics` (minimal Prometheus).
- **4.7** Config immutability is now a SQLite trigger: UPDATE of any
  identity-defining column on a non-draft config aborts. Rotation INSERTs new
  rows, so it composes. Lifecycle/calibration/detector/label columns stay
  mutable by design.
- **Phase 5 offline**: `GET /v1/bundle?model=` exports configs + null models +
  published detectors (configs without a detector ship as `skipped` with the
  reason — never silently dropped). `markwright.verify.offline`:
  `load_bundle`/`verify_offline` run the standard pipeline with
  `mode="offline"`, tokenizer from local HF cache only (`local_files_only`),
  FPR may only tighten. CLI: `bundle export`, `verify --offline --bundle`.
- RBAC verbs added: key.read (admin/operator/auditor), key.destroy (admin).
- Tests: `tests/test_lifecycle_and_evidence.py` — 15 new tier-1 tests
  (rotation/revocation/destroy semantics, purpose separation, audit restart +
  export tamper detection, detector disclosure, batch expected-FP, result
  scoping, policy RBAC, immutability trigger, offline bundle incl. skip list
  and FPR tightening). ParityAdapter gained a trivially-publishable detector
  so all of this runs without torch. Suite: 80 passed.
- Deliberately deferred: chain-head column on results (4.8 note), threshold
  custody / approvals (v2), async batch (v1), X25519 sealing (v1).

### Phase 6 — dashboard, two screens (commit: phase-6)
- Added `GET /v1/configs?model=&status=` (design §8.1; was missing) with the
  same role filtering as the single-config route; tier-1 test added.
- `dashboard/`: Vite + React + TS, NO extra runtime deps (plain fetch client in
  `src/api.ts`, hand-typed against schemas/ — OpenAPI-generated client is v1).
  Screens: **Verify** (text or token-ids input, fpr tighten-only field, result
  card, per-candidate table) and **Registry** (models × configs, status chips,
  calibration/min-detectable/detector columns). Role switcher uses the dev
  bearer tokens; responsible-use text is a permanent footer.
- Design §8.3 UI rules are encoded in CSS, not convention: ONE hue (indigo) at
  varying weight for bands, `no_evidence` styled IDENTICALLY to
  `insufficient_evidence`, no green/red anywhere; the caveats block and FPR
  target are plain in-flow elements (no <details>, no toggles — structurally
  non-collapsible); every p-value renders with its 95% interval.
- `dashboard/Dockerfile` (node build → nginx with /v1 proxy to the api
  service), so `docker compose --profile dev up` can now actually build all
  services. `.claude/launch.json` added for the dev server.
- VERIFIED LIVE in a browser: seeded a real API (demo.py), generated marked
  ids with the deployment key, pasted into the Verify screen → MODERATE
  EVIDENCE card with p=3.4e-3 + interval, mean_g=0.825 in the candidate table,
  caveats shown; Registry shows demo-llm/active with the verifier role view
  (calibration ref withheld). Screenshot-checked both screens.
- Suite: 81 passed; dashboard `npm run build` clean.

### Where this leaves the repo (end of session 2)
Milestones A and B are complete. The remaining plan items are the design's own
v1/v2 scope (OIDC, Postgres, cloud key backends, more schemes, threshold
custody, robustness sweeps, X25519 sealing, async jobs, SDKs, Helm templates,
dashboard screens 3–7). Next session: pick from design §11 v1 list; suggested
order: vLLM hook plugin → KGW adapter as the architecture test (design §12
"adding a scheme") → OIDC.

### Phase 8 (Milestone C start) — vLLM plugin + KGW package (commit: phase-8)
- `src/markwright/inference.py`: the inference-hook helpers moved into the
  package (hooks stay thin scripts). `VLLMLogitsProcessor` wraps any adapter's
  HF-style processor into vLLM's per-request logits-processor convention;
  supports both `(token_ids, logits)` and `(prompt_ids, generated_ids, logits)`
  arities; empty context passes logits through. `hooks/vllm_example.py` shows
  SamplingParams wiring. VERIFICATION HONESTY: tested against a simulated vLLM
  sampling loop (exact calling convention; marked output detects at mean_g>0.72,
  and the wrapper's output is torch.equal to the HF batch path) — but vLLM
  itself does not install on macOS, so live-under-vLLM remains UNVERIFIED until
  someone runs hooks/vllm_example.py on a Linux/CUDA host.
- `packages/markwright-scheme-kgw/`: the design-§12 architecture test, done
  properly as a SEPARATE pip package with its own pyproject and
  `markwright.schemes` entry point. Core diff for scheme support: zero lines.
  Prototyped against transformers 5.21 first (WatermarkDetector needs a
  PretrainedConfig with explicit bos_token_id — None breaks torch.any; we
  default bos to vocab_size-1 = gpt2's eos). KGW facts encoded: not
  distortion-free; no publishable detector (export_detector→None, publish
  route → 409, Detector-input detect() raises); z statistic re-calibrated
  through the null model like any other; HF's `prediction` boolean and
  `confidence` are dropped (honesty rule 1), with a test proving it.
- Demo step 9: second scheme registered/calibrated/activated next to synthid;
  verify scans both (Bonferroni ×2) and synthid-marked text matches only the
  synthid config. Measured: marked kgw z≈14.8 right key / z≈−1.1 wrong key.
- CI tier-2 installs the kgw package. Suite: 92 passed (11 new kgw/vllm tests).
- Next: 8.3 OIDC → 8.4 Postgres → 8.5 Vault Transit (plan, Milestone C).
