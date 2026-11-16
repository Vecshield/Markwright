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

### Phase 8.3 + 8.5 — OIDC auth + Vault Transit backend (commit: phase-8b)
- **OIDC (8.3)**: `api/auth.py` `OIDCValidator` — authlib JWT validation
  against the issuer's JWKS (discovery-fetched, injectable for tests);
  iss/aud/exp enforced with 30s leeway; role claim is a dotted path
  (default "roles", Keycloak "realm_access.roles"); token roles must contain
  one of the four Markwright role names (exactly the realm roles our bundled
  keycloak-realm.json defines); multiple roles → highest privilege.
  Wiring: MARKWRIGHT_OIDC_ISSUER configures it; once set, STATIC DEV TOKENS
  STOP WORKING. Break-glass per design §6.3: MARKWRIGHT_BREAK_GLASS=1
  re-admits them, never in the provider profile, each use audited
  (auth.login/break-glass-static-token). 11 tier-1 tests mint real RS256
  tokens against a local JWKS: role mapping, every rejection path (expired,
  wrong aud/iss, bad signature, no role, garbage), API integration incl.
  created_by carrying the JWT principal, break-glass on/off/provider.
- **Vault Transit (8.5)**: `keys/vault_transit.py` implements KeyBackend
  against Vault/OpenBao transit. Root keys non-exportable (aes256-gcm96 —
  hvac's client validation rejects the newer `hmac` type; Vault keeps a
  distinct per-version HMAC key for any type); scheme material =
  HKDF-Expand (RFC 5869 §2.3) with transit/hmac as the PRF, so DERIVATION
  NEVER LEAVES THE BOUNDARY; rotate maps to transit rotation (old versions
  keep verifying); revoke is registry-state only BY DESIGN (revoked keys must
  keep verifying; Vault-side nothing to cut); destroy trims + deletes.
  6 integration tests against a REAL OpenBao container (marker `network`,
  skip when :8200 absent): determinism, info separation, multi-block expand,
  relative version monotonicity (dev server keeps state between runs — tests
  assert relative, not absolute, versions), non-exportability, destroy, and
  the FULL control plane (parity loop + signed audit export) with every
  secret in Vault. Container: `docker run -d --rm --name mw-openbao -p
  8200:8200 -e BAO_DEV_ROOT_TOKEN_ID=dev-root openbao/openbao:2`.
- **Backend dispatch fix (found by the transit test)**: derivation paths
  called `vault()` (the deployment default) regardless of which backend a key
  row RECORDS. New `backend_for(name)` resolves per record; `vault()` is only
  where NEW keys are created (MARKWRIGHT_KEY_BACKEND env). Without this, a
  backend migration would silently derive garbage from the wrong store.
- compose: api service now passes through KEY_BACKEND / VAULT_ADDR+TOKEN /
  OIDC_* env (onprem defaults point at the openbao service + Keycloak-style
  role claim). pyproject: `oidc` extra (authlib); dev extra includes
  authlib+hvac so tier-1 covers OIDC.
- Suite: 103 passed tiers 1+2; +6 transit tests behind `network`.
- **8.4 Postgres: deliberately deferred** to the next session — it is a
  storage swap behind an already-narrow RegistryStore interface, but a
  faithful port (SQLAlchemy + the immutability trigger + placeholders) is a
  rewrite that deserves its own phase with a real PG container, not a
  tail-of-session rush.

### Phase 8.4 — Postgres RegistryStore (commit: phase-8c)
- `registry/store.py` rewritten on SQLAlchemy Core. `RegistryStore` accepts a
  file path / ":memory:" (SQLite, fully back-compat) or any SQLAlchemy URL
  (postgresql+psycopg). New `Database` wrapper: named-parameter text SQL in,
  list-of-dicts out, one transaction per statement (matching the previous
  autocommit behaviour; noted that multi-statement ops are not atomic — same
  as before). sqlite uses StaticPool + check_same_thread=False so :memory:
  behaves as a single shared connection.
- Immutability trigger per dialect: SQLite `UPDATE OF ... WHEN` as before;
  Postgres plpgsql function + trigger with IS DISTINCT FROM. Both surface as
  a uniform `ImmutableConfigError` via the wrapper. DIALECT NUANCE recorded in
  the PG test: sqlite fires when a guarded column is MENTIONED, plpgsql when a
  value actually CHANGES — no-op updates pass on PG, real mutations abort on
  both. (PG semantics are the more correct reading of "immutable".)
- AuditChain now persists through the same Database wrapper (works on both
  engines unchanged). Raw SQL in app.py/tests replaced with store methods
  (get_signing_key, get_model_version, result_counts_by_band,
  config_counts_by_status).
- sqlalchemy>=2.0 promoted to a core dependency (the registry is core);
  psycopg stays in the `postgres` extra.
- Tests: full tiers 1+2 pass unchanged on the SQLAlchemy store (103).
  `tests/test_postgres.py` (marker network) ran green against a real
  postgres:16 container: store roundtrip incl. nested JSON + supersedes
  chains, plpgsql trigger (abort + rollback + draft-editable), accepted-null-
  model hydration join, result filters, and the FULL HTTP control-plane loop
  on PG including audit-chain restart survival and signed evidence export.
  CI gained a `postgres-integration` job (GH Actions service container) so
  the PG path is enforced, not just locally exercised.
  Container: `docker run -d --rm --name mw-postgres -p 5433:5432
  -e POSTGRES_USER=markwright -e POSTGRES_PASSWORD=test
  -e POSTGRES_DB=markwright postgres:16-alpine`.
- Deferred: Alembic migrations (fresh deployments only for POC; the plan's
  v1 list keeps it), SQLAlchemy-native JSON columns (TEXT is portable and the
  wire format anyway).

### Where this leaves the repo (end of session 3)
Milestone C: 8.1 vLLM wrapper ✓, 8.2 KGW package ✓, 8.3 OIDC ✓,
8.4 Postgres ✓, 8.5 Vault Transit ✓. The design's v1 gate is now substantially
met on the infra side. Remaining v1/v2 (plan): threshold custody, calibration
robustness sweeps (marked corpus + edit fractions), X25519 session sealing,
async jobs (arq/APScheduler), Alembic, SDKs, Helm templates, dashboard
screens 3–7, Unigram/Kuditipudi schemes. Suggested next: calibration
robustness sweeps (fills min_detectable_tokens, the one null field on every
result card) or X25519 sealing (closes the MVP's one security shortcut).

### Repo polish — brand kit + Vecshield-style presentation (commit: polish-brand)
Context: Markwright keeps its name and moves under the Vecshield org
(github.com/Vecshield). Polish takes the presentation conventions from the
vecshield repo (minimal badges, logo-first README, problem-statement hook,
early "Try it", an explicit "What this does not do" section, data-residency
messaging, architecture tree, CONTRIBUTING/SECURITY/CODE_OF_CONDUCT) and the
visual identity from the Markwright brand kit the user supplied
(Markwright_Complete_Assets_2026.zip — guide, logos, favicons, banners, theme
tokens; the kit's integrity rules mirror the product honesty rules).
- `assets/brand/`: vendored subset (github-readme banner svg+png, social/og
  pngs, wordmarks, symbol, tokens.css/json, BRAND_GUIDE.md). Full kit stays
  outside the repo.
- README rewritten in the house style: banner, 4 badges (incl. a "not an AI
  detector" badge — the anti-claim IS the positioning), hook, 2-minute
  "Try it" via demo.sh, "What you get", "What this does not do",
  "Your text stays yours" (residency), architecture tree with the
  schemes-are-packages callout, honesty rules, community links, status.
- CONTRIBUTING.md (dev setup, tiers incl. infra containers, review rules,
  phased workflow, DCO), CODE_OF_CONDUCT.md (Contributor Covenant 2.1 + a
  project-specific no-surveillance clause), SECURITY.md upgraded from stub to
  a real policy with severity tiers specific to the product's promises
  (key-material escape = P0, text storage = P0, audit forgery/honesty bypass
  = P1, RBAC/OIDC = P2; watermark removal explicitly out of scope).
- Dashboard rebrand to the kit's Frost theme: brand favicons + webmanifest in
  dashboard/public, wordmark in the header, palette switched to the kit's
  exact evidenceBands tokens (strong #2463C7 → weak #98CFFF, blue intensity
  only). ONE deliberate deviation from the kit, recorded here: the kit gives
  no_evidence its own tint; design §8.3 and the kit's own principle no. 2
  require no_evidence and insufficient_evidence to be IDENTICAL — both use
  #C5E6FF with ink text.
- Verified live in the browser: both screens render under the new theme; a
  text-path verify of digit soup over both schemes correctly returned
  no_evidence with Bonferroni + caveats (the honest path, by accident).

### Dashboard v2 — full operator console (commit: dashboard-v2)
Scope per user direction: Markwright remains fully independent (its GitHub org
home is organizational only — zero cross-project integration); the dashboard
grew from 2 screens to a 6-screen console; the CUSTODY concept mark replaces
TRACE as the product icon (favicon + header); themes are the brand kit's
Midnight (default, dark console) and Frost (light), toggle in the header,
persisted. All plain React + fetch — still zero runtime deps beyond react.
- Screens: **Overview** (describes the system: what it is, the
  gate→candidates→detect→calibrate→Bonferroni→band pipeline strip, honesty
  rules, the evidence single-hue scale, live deployment facts from
  /readyz+policy); **Verify** (unchanged); **Registry** (click a config row →
  drawer with params/tokenizer-hash/key handle + role-gated lifecycle actions:
  run/accept calibration, activate, deprecate, retire, publish detector behind
  a confirm that spells out the spoofing consequence); **Onboard** (the §8.3
  5-step wizard — register → scheme cards with honest capability notes →
  config+key → calibrate w/ provisional warnings → activate, each step printing
  the artifact it produced); **Keys** (lineages grouped, state chips, rotate
  operator / revoke admin-with-reason, purpose separation visible); **Admin**
  (policy editor w/ floor warning, verification activity metadata, audit tail
  with head hash).
- api.ts covers the full surface incl. keys/calibrations/policy/audit/readyz;
  ApiError surfaces RBAC denials as friendly banners.
- Fixed: /readyz and /metrics were missing from the vite proxy AND the nginx
  conf (only /v1 and /healthz were proxied) — Overview's deployment panel
  showed dashes until then.
- VERIFIED LIVE end to end in the browser as operator: the wizard ran
  register→scheme→config+key→calibrate(900 docs, floor 3.4e-3, provisional
  notes rendered)→accept→activate, ending at "ui-llm 1.0 is live" with the
  generation-profile pointer; Keys shows the signing identity without
  lifecycle actions and generation keys with role-gated Rotate/Revoke; Admin
  (Frost theme, admin role) shows the policy editor and the real audit trail
  of every click. Build clean (tsc + vite, 174 kB js).

### UI plan + wording pass (commit: ui-plan-wording)
- `docs/ui-plan.md`: Track A is a copy style guide (say it once; one corrective
  statement per surface; no reader-facing jargon or abbreviations; numbers in
  plain language first; no em dashes in product copy; the two required product
  sentences stay). Track B is the flow roadmap ordered by value: Verify
  (example inputs, live length feedback, scope filters, permalinks), Onboard
  (resumable, inline validation, determinate progress), Registry
  (search/filters, stale-calibration notice), Keys (validity timeline,
  rotation preview), Admin (policy-change preview, audit download), the
  Calibration screen (blocked on robustness sweeps), platform polish
  (toasts, skeletons, prefers-color-scheme, a11y, Playwright smoke).
- Wording audit applied across backend and UI. Backend: band texts and
  band_for reasons rewritten in plain words ("the calibrated p-value is below
  the false-positive target" instead of "p <= fpr_target"); the STRONG text no
  longer repeats the not-proof clause the always-on caveat carries; the
  required no-evidence sentence stays ("Absence of a mark is not evidence of
  human authorship"); INSUFFICIENT explains "too predictable" alongside the
  jargon; calibration notes and the FastAPI/CLI descriptions reworded; caveat
  display texts rewritten in both CLI and dashboard (keys unchanged: wire
  contract). UI: "fpr 1e-2" chip is now "false-positive target 1 in 100"
  (fmtFpr rounds odds DOWN), pipeline steps say "length check" and "multi-scan
  correction", the Overview rules card re-written without the gate jargon,
  em dashes removed from product copy, README heavy spots rewritten (sample
  output now matches the real CLI output), quickstart's "That is the point."
  closer replaced with the fact it gestured at.
- Tests: all asserted substrings kept on purpose ("not evidence of human
  authorship", "calibration resolves p down to", "expected by chance");
  103 passed, dashboard build clean.

### UI fixes + demos + scheme catalogue (commit: ui-schemes-demos)
Reported: "lots of errors in the UI and no dynamic updates when I change the role".
Both reproduced in the browser before changing anything.
- ROOT CAUSE 1 (no dynamic updates): every screen's fetch ran in a
  `useCallback(..., [])` / `useEffect(..., [])`, so changing the role token
  never refetched. Fixed in `App.tsx` by keying each screen on the auth token
  (`key={token}`), which remounts it as the new principal; verified live
  (verifier → operator on Keys went from 0 tables to 3 tables and 2 Rotate
  buttons with no reload).
- ROOT CAUSE 2 (lots of errors): the 403s were correct RBAC, but every screen
  rendered them as error banners. Added `components.tsx` with `useAsync`
  (separates forbidden from error), `Panel` (one place deciding
  loading/forbidden/error/content), `Restricted` (calm notice naming the roles
  that can see it), `ErrorBanner` and `Toast`. Keys and Admin converted; nav
  dims screens the role cannot fully use. Verified: verifier on Admin now shows
  0 errors, a read-only policy with an explanation, a disabled Save, and one
  notice for the audit section.
- DEMOS: new `GET /v1/samples` generates presets ON the running deployment: a
  marked sample driven through the newest active configuration's own logits
  processor (so it carries that deployment's key), a human passage from the
  bundled corpus, and a 30-token slice. Absent pieces are reported in `notes`
  rather than silently omitted. The Verify screen shows them as buttons with
  the expected outcome and the reason. Verified live: marked → MODERATE
  EVIDENCE, human → NO EVIDENCE, short → INSUFFICIENT EVIDENCE.
- SCHEME CATALOGUE: `src/markwright/schemes/explain.py` is one source of truth
  for plain-language explanations (how it marks, how it detects, statistic, key
  material, strengths, limits, parameters, citation), surfaced by
  `GET /v1/schemes/catalogue` and on `GET /v1/schemes`. New Schemes screen
  renders it with an inline SVG diagram of the onboarding path
  (register → configure → key → calibrate → accept → activate) and notes the two
  rules that path enforces. Entries exist for schemes we do NOT ship (unigram,
  kuditipudi-exp) so an operator can compare before adding one. `docs/schemes.md`
  carries the same material for readers outside the app.
- `ParityAdapter` gained a real `apply()` (biases even token ids, which is what
  it detects), so the samples endpoint is testable without torch.
- Tests: 3 new (catalogue shape incl. absent schemes, explanations on
  /v1/schemes with parity-test correctly having none, samples generated and
  actually verifying as labelled). 106 passed, ruff clean, dashboard builds.

### UI graphics + readable samples (commit: ui-graphics)
Feedback: result card too text-heavy, an underline under the wordmark, presets
showing token ids instead of text, and a bare "TypeError: Failed to fetch".
- "Failed to fetch" was the API being down. `api.ts` now catches the bare
  TypeError that fetch() raises when nothing answers and returns an ApiError
  saying how to start the server. (The raw TypeError was never actionable.)
- Presets are prose now: `/v1/samples` decodes bundled corpus ids with the
  cached gpt2 tokenizer and returns `kind: "text"` with the decoded passage;
  the Verify screen loads text mode for those. The marked sample stays token
  ids because it is generated by the watermark sampler with NO language model
  behind it, and the UI says exactly that rather than pretending otherwise. A
  missing tokenizer degrades to ids with a stated note.
- New `charts.tsx`, pure SVG, no dependency, obeying §8.3 (one hue at varying
  intensity, nothing shaped like pass/fail): EvidenceScale (band marker, with
  "not measured" set visually apart from the ordered scale because it is the
  absence of a measurement), PValueAxis (log axis, the deployment target as a
  dashed line, the 95% interval drawn as a band around the point), TokenGauge
  (scored vs total vs minimum detectable), CandidateChart (dot per
  configuration scanned, matched one emphasised). Verified live: a marked
  sample puts kgw left of the target and synthid-text far right, which reads
  instantly.
- Raw per-configuration numbers moved into a <details>; the p-value, interval,
  target and caveats all stay in flow, so the honesty rules are untouched.
- Overview landing: stat tiles (models, calibrated configurations, schemes,
  target) replace a definition list, and the real EvidenceScale component
  illustrates the scale.
- Wordmark underline removed (it had become an <a> for navigation).
- 106 tests green (samples test now asserts prose, not ids), ruff clean.
