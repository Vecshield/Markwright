# Markwright POC plan

Agreed 2026-10-28. Objective: a **working end-to-end POC** (register → key →
marked generation → provisional calibration → activate → verify with an honest
band) plus infra parity with the design doc, **without** investing in
calibration quality. Calibration is treated as a one-ticket unblock with honest
labelling, not a workstream. Progress is logged in
[IMPLEMENTATION_LOG.md](IMPLEMENTATION_LOG.md).

Key enabling fact: SynthID detection needs torch but **no model weights** —
`compute_g_values()` consumes token ids + key list, no forward pass. So
`detect()` accepting token ids (design §5 already says `text: str | TokenIds`)
makes the *real* adapter testable deterministically offline, and makes the null
model computable without hosting a model.

## Milestone A — working POC

### Phase 0 · Make it a project
- [x] 0.1 `git init`, baseline commit of the scaffold
- [x] 0.2 Full Apache-2.0 LICENSE text (was a 2-line stub)
- [x] 0.3 CI: ruff + tier-1/2 pytest (`.github/workflows/ci.yml`)
- [x] 0.4 Repo-local `CLAUDE.md` (code structure + agentic dev organization)
- [x] 0.5 pyproject: pytest markers `torch`/`slow`/`network`; ship `schemas/` in the wheel

### Phase 1 · Unblock the pipeline
Deadlock at baseline: `STATE["null_models"]` never written; `transition()`
requires `calibration_ref` which nothing sets → no activation, verify always
`insufficient_evidence`.
- [x] 1.1 `detect()` accepts `TokenIds | str` (design §5); tokenizer loaded only for str input
- [x] 1.2 Bundled starter null corpus `data/null/` (public-domain Gutenberg text, pre-tokenized gpt2 ids, lengths ~{100,200,400}) — small and honest about it
- [x] 1.3 `NullModel.resolution_floor()` + `quality: provisional|accepted`; `band_for` caps STRONG/MODERATE when `fpr_target < floor`; new `provisional_calibration` caveat (schema updated)
- [x] 1.4 `POST /v1/configs/{id}/calibrations` (synchronous), `POST /v1/calibrations/{run}:accept`; null models persisted (SQLite `calibrations` table) and loaded at startup; bootstrap bucket-fits precomputed at calibration time so `p_value_interval` is cheap at verify time
- [x] 1.5 CLI `markwright calibrate run|show|accept`
- [x] 1.6 Activation with provisional calibration allowed in `dev`/`onprem`, refused in `provider` profile
- [x] 1.7 Dev-profile default `fpr_target=1e-2` (floor of a ~300-doc corpus is ~3e-3); `MARKWRIGHT_FPR_TARGET` env respected

### Phase 2 · Close the generation loop
- [x] 2.1 `GET /v1/models/{id}/versions/{v}/generation-profile` (returns scheme params + sealed-material hex, MVP form; X25519 session sealing = v1); audited as `key.handle_issue`
- [x] 2.2 Fix `hooks/hf_generate_example.py` to the real response shape; `--max-new-tokens`, `--seed`
- [x] 2.3 CLI `markwright generate test --config --prompt`

### Phase 3 · Tokenizer integrity
- [x] 3.1 Delete the `len(text.split())` fallback; tokenizer hash recorded at calibration, compared at verify; mismatch/missing → `tokenizer_uncertain` + `insufficient_evidence`
- [x] 3.2 Per-candidate token counting (was: counted once with `candidates[0].tokenizer_ref`)

### Phase 7 (A-part) · Mock tests → real tests
- [x] 7.1 Invert the two baseline assertions that enshrined the deadlock
- [x] 7.2 Tier 1: `ReplayAdapter` replays *recorded real* detections from `tests/fixtures/detections/`; registry lifecycle state-machine tests; RBAC matrix (4 roles × verbs); responses validated against `schemas/*.json`; floor/caveat tests; tokenizer-mismatch tests
- [x] 7.3 Tier 2 (`-m torch`, no network/weights): real SynthID adapter on checked-in token-id fixtures — marked ≫ 0.5, unmarked ≈ 0.5; wrong-key non-detection; truncation degradation; export_detector round-trip
- [x] 7.4 `tests/fixtures/regenerate.py` — fixtures are regenerable artifacts, not magic numbers

**Milestone A exit gate** ✅ PASSED 2026-10-28 — `scripts/demo.sh`: register → key → marked
generation (or fixture replay when no model) → calibrate provisional → activate
→ verify marked (band > no_evidence) → verify human (no_evidence) → verify 30
tokens (insufficient) → audit chain verifies. This is design §11's MVP gate.

## Milestone B — infra parity (NOT STARTED)

### Phase 4 · API + service surface
- [ ] 4.1 Key lifecycle routes: `POST /v1/keys`, `:rotate`, `:revoke`, `:destroy`, `GET /v1/keys?lineage=`; enforce `generate_until`/`verify_until`; `historical_key` flag on rotated keys
- [ ] 4.2 CLI `key create|show|rotate|revoke`
- [ ] 4.3 Persist + sign the audit chain (SQLite-backed; Ed25519 deployment identity created at first boot into the vault; `GET /v1/audit/export` signed tar; CLI `audit tail|export|verify`)
- [ ] 4.4 `ArtifactStore` protocol + local-fs impl (null models, calibration reports, detectors)
- [ ] 4.5 `POST /v1/configs/{id}/detector:publish`, `GET /v1/detectors/{id}`, CLI `detector publish`; sets `labels.disclosure=public-detector` → `spoofing_not_excluded`
- [ ] 4.6 `PUT /v1/policy`, `/readyz`, `/metrics`, `POST /v1/verify/batch` (+ expected-FP count), `GET /v1/verify/results`
- [ ] 4.7 Config immutability as a DB-level guard, not a convention
- [ ] 4.8 Audit-chain restart test; results persisted with chain head

### Phase 5 · Offline verification
- [ ] `markwright bundle export` + `markwright verify --offline --bundle` (design §7.5)

### Phase 6 · Dashboard (two screens)
- [ ] Vite+React+TS, generated OpenAPI client; Verify screen rendered from `schemas/verification-result.json` (non-collapsible caveats/FPR, one hue, no green/red); Registry table. Other screens = v1.

### Deferred to v1/v2 (per design §11, not parity failures)
OIDC/RBAC beyond static tokens · Postgres · Vault/KMS/HSM backends · other six
schemes · threshold custody · robustness sweeps · SDKs · Helm templates · Rekor/C2PA.

## Known correctness items folded into phases
- `p_value_interval` refit 200×/verify → precompute bootstrap bucket-fits at calibration (phase 1.4)
- `NullBucket.p_value` magic `1e-3` clamp → explicit, documented floor (phase 1.3)
- `no_evidence` dropped its p-value (`best=None`) — §7.3 implies it keeps one (phase 1.3)
- `jobs.worker` referenced by compose but absent → stub module with a clear error (phase 0)
