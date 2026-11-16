# OWM 0.1: formal data model

A mathematical presentation of the entities in `schemas/`, their value
domains, the relations between them, and the invariants that must hold. This
document is the formal companion to [owm-0.1.md](owm-0.1.md); where the two
disagree, the JSON Schemas are normative and this is in error.

Notation: `A → B` total function, `A ⇀ B` partial function, `℘(A)` powerset,
`A*` finite sequence, `A?` optional (`A ∪ {⊥}`). Attribute access is `e.a`.

---

## 1. Base domains

```
Id        = { s ∈ Σ*          | s matches ^[a-z0-9][a-z0-9._-]{1,63}$ }
Uuid      = { s               | s matches RFC 4122 }
Time      = RFC 3339 instants, totally ordered by <
Hash      = { s ∈ {0..9,a..f}^64 }
Digest    = { "sha256:" · h   | h ∈ Hash }
Hex       = { s ∈ {0..9,a..f}* , |s| even }
Json      = the usual recursive JSON value domain
Schema    ⊂ Json              JSON Schema 2020-12 documents
Prob      = [0,1] ⊂ ℝ
Fpr       = (0, 0.05] ⊂ ℝ     admissible false-positive targets
```

Four **open vocabularies**. Each is a finite registered set plus an extension
set `X_· = { "x-" · s }`, so a validator distinguishes a declared extension
from a typo:

```
Modality  = {text, image, audio, video}                      ∪ X_mod
Unit      = {token, pixel, patch, latent, sample, frame}     ∪ X_unit
Stage     = {logits, sampling, posthoc,
             latent_init, component_weights}                 ∪ X_stage
Surface   = {hf-transformers.generate, hf-transformers.logits-processor,
             vllm.logits-processor, sglang.logits-processor, tgi.sidecar,
             llama-cpp.sampler-hook, openai-proxy,
             hf-diffusers.pipeline-callback, hf-diffusers.component-swap,
             posthoc.transform}                              ∪ X_surf
```

Closed vocabularies (extension is a spec revision, not an `x-` prefix):

```
Codec     = {tokenizer, raster, latent-space, pcm} ∪ X_codec
StatType  = {mean, z_score, bit_accuracy, distance,
             log_odds, permutation_cost} ∪ X_stat
Dir       = {higher_is_marked, lower_is_marked}
Status    = {draft, active, deprecated, retired}
KeyState  = {pending, active, rotating, verify-only, revoked, destroyed}
Purpose   = {generation, signing}
Backend   = {aws-kms, gcp-kms, azure-keyvault, vault-transit,
             pkcs11, local-sealed}
Custody   = {single, split-knowledge, threshold}
Band      = {strong_evidence, moderate_evidence, weak_evidence,
             no_evidence, insufficient_evidence}
Role      = {admin, operator, verifier, auditor, system}
Outcome   = {ok, denied, error}
Caveat    = {short_text, low_entropy, possible_paraphrase,
             tokenizer_uncertain, multiple_candidates, historical_key,
             spoofing_not_excluded, provisional_calibration,
             result_is_probabilistic}
Expect    = {survives, weakens, defeats, unknown}
IntStatus = {supported, planned, unsupported}
Spoof     = {forgeable, not_applicable, resistant}
DescStat  = {implemented, specification-only, experimental}
Guidance  = {trust, calibrate}
Mode      = {server, offline}
```

**Band is totally ordered** on the measured sub-chain, with
`insufficient_evidence` incomparable (it denotes the absence of a
measurement, not a small one):

```
no_evidence ≺ weak_evidence ≺ moderate_evidence ≺ strong_evidence
insufficient_evidence  ∥  b     for all b
```

---

## 2. Entities

### 2.1 Scheme descriptor — `D`

The scheme author's document; `schemas/scheme-descriptor.json`.

```
Carrier   = ⟨ unit: Unit, codec: Codec?, min_units: ℕ⁺?, constraints: Json ⟩
Embedding = ⟨ stage: Stage, distortion_free: 𝔹 ⟩
KeyModel  = ⟨ requires_key: 𝔹, publishable_detector: 𝔹,
              backend_mac: 𝔹, detection_artifact: Σ*?,
              material: ⟨shape, derivation, info_template⟩? ⟩
Statistic = ⟨ name: Σ*, type: StatType, dir: Dir ⟩
Detection = ⟨ stat: Statistic, analytic: ⟨available: 𝔹, assumptions: Σ**,
              guidance: Guidance⟩?, needs_model: 𝔹, cost: {low,medium,high} ⟩
Params    = ⟨ schema: Schema, defaults: Json?, notes: Σ* ⇀ Σ* ⟩
Binding   = ⟨ surface: Surface, stage: Stage, status: IntStatus,
              adapter: Σ*? ⟩
Declared  = ⟨ transform: Σ*, expected: Expect ⟩

D = ⟨ owm: {"0.1"}, kind: {"scheme-descriptor"},
      scheme_id: Id, namespace: Σ*?, version: Σ*, title: Σ*,
      modality: Modality, carrier: Carrier, embedding: Embedding,
      key_model: KeyModel, detection: Detection, parameters: Params,
      robustness: ℘(Declared), integrations: Binding*  (nonempty),
      spoofing: ⟨after_publication: Spoof⟩?, status: DescStat ⟩
```

### 2.2 Deployment manifest — `C`

The operator's registry row; `schemas/model-config.json`. Immutable once it
leaves `draft`.

```
VDefaults = ⟨ fpr_target: Fpr, min_tokens: ℕ⁺,
              min_detectable_tokens: ℕ⁺?, tokenizer_hash: Hash? ⟩

C = ⟨ config_id: Uuid, model_id: Id, model_version: Σ*,
      model_fingerprint: Digest?, config_version: ℕ⁺, supersedes: Uuid?,
      scheme_id: Id, scheme_version: Σ*?, scheme_params: Json,
      modality: Modality, carrier: Carrier?, tokenizer_ref: Σ*?,
      key_ref: Uuid, detector_ref: Σ*?, calibration_ref: Uuid?,
      verification_defaults: VDefaults, status: Status,
      generate_from: Time?, generate_until: Time?, verify_until: Time?,
      labels: Σ* ⇀ Σ*, created_at: Time, created_by: Σ* ⟩
```

### 2.3 Key record — `K`

`schemas/key-record.json`. Holds a *handle*; the secret is never an attribute.

```
Derivation = ⟨ kdf: {hkdf-sha256}, info: Σ*, outputs: Σ* ⇀ Σ* ⟩
CustodySpec = ⟨ mode: Custody, threshold: ℕ≥2?, shares: ℕ≥2?,
                custodians: Σ** ⟩
Rotation   = ⟨ interval_days: ℕ⁺, overlap_days: ℕ, auto: 𝔹 ⟩

K = ⟨ key_id: Uuid, purpose: Purpose, owner_model_id: Id?,
      backend: Backend, backend_ref: Σ*, key_version: ℕ⁺,
      lineage_id: Uuid, derivation: Derivation?, custody: CustodySpec?,
      state: KeyState, valid_from: Time, generate_until: Time?,
      verify_until: Time?, rotation_policy: Rotation?,
      public_detector_ref: Σ*?, created_at: Time, created_by: Σ*,
      revoked_at: Time?, revocation_reason: Σ*? ⟩
```

### 2.4 Calibration run — `R`

```
Bucket = ⟨ length: ℕ⁺, n: ℕ, has_tail: 𝔹 ⟩
Quality = {provisional, standard}

R = ⟨ run_id: Uuid, config_id: Uuid, status: {ran, accepted},
      null_corpus_ids: Id*, n_docs: ℕ, quality: Quality,
      resolution_floor: Prob, buckets: Bucket*, fpr_target: Fpr,
      tokenizer_ref: Σ*?, tokenizer_hash: Hash?,
      min_detectable_tokens: ℕ⁺?, null_model: Json,
      notes: Σ**, created_at: Time, created_by: Σ* ⟩
```

### 2.5 Verification result — `V`

```
Cand = ⟨ config_id: Uuid, scheme_id: Id, key_version: ℕ⁺,
          statistic: ⟨name: Σ*, value: ℝ, extra: Json⟩,
          p: Prob, p_adj: Prob, ci95: (Prob × Prob)?,
          min_detectable_tokens: ℕ⁺?, null_model_ref: Σ* ⟩

V = ⟨ result_id: Uuid, request_id: Uuid?, requested_at: Time,
      requested_by: Σ*?, text_digest: Hash?,
      modality: Modality, units: ⟨unit: Unit, total: ℕ, scored: ℕ⟩?,
      token_count: ℕ, scored_token_count: ℕ,          ⟵ text aliases
      fpr_target: Fpr, band: Band, band_reason: Σ*,
      best_match: Uuid?, candidates: Cand*,
      caveats: ℘(Caveat), engine: ⟨version: Σ*, mode: Mode⟩,
      signature: Hex? ⟩
```

### 2.6 Audit entry — `A`

```
Action = {config.create, config.activate, config.deprecate, config.retire,
          key.create, key.rotate, key.revoke, key.destroy,
          key.handle_issue, key.custody_change, detector.publish,
          calibration.run, calibration.accept, verify.single, verify.batch,
          export.evidence, auth.login, auth.denied, policy.change, anchor}

A = ⟨ seq: ℕ, ts: Time, actor: ⟨principal: Σ*, role: Role⟩,
      action: Action, target: ⟨kind: Σ*, id: Σ*⟩, outcome: Outcome,
      details: Json, prev_hash: Hash, entry_hash: Hash, signature: Hex? ⟩
```

### 2.7 Repo declaration — `P`

The public-safe projection published in a model repository.

```
Marking = ⟨ modality: Modality, scheme_id: Id, scheme_version: Σ*?,
            config_fingerprint: Digest, params: Json?,
            active_from: Time?, active_until: Time?,
            verification: ⟨detector: ⟨uri,sha256⟩?, endpoint: Σ*?,
                           bundle: ⟨uri,sha256⟩?⟩,
            spoofing_not_excluded: 𝔹 ⟩

P = ⟨ owm: {"0.1"}, kind: {"repo-declaration"},
      subject: ⟨uri: Σ*, revision: Σ*?, artifacts: Σ* ⇀ Digest⟩,
      marking: Marking*  (nonempty),
      policy: ⟨fpr_target: Fpr?, contact: Σ*?⟩?,
      generated_by, generated_at: Time?,
      public_key_hex: Hex?, signature: Hex? ⟩
```

---

## 3. Relations

Let `𝒟, 𝒞, 𝒦, ℛ, 𝒱, 𝒜` be the sets of stored instances. The relational
structure is four partial functions and one tree:

```
scheme   : 𝒞 → 𝒟        c ↦ the D with D.scheme_id = c.scheme_id
key      : 𝒞 → 𝒦        c ↦ the K with K.key_id   = c.key_ref
calib    : 𝒞 ⇀ ℛ        c ↦ the R with R.run_id   = c.calibration_ref
target   : ℛ → 𝒞        r ↦ the C with C.config_id = r.config_id
lineage  : 𝒦 → Uuid     K ↦ K.lineage_id                 (equivalence classes)
supersede: 𝒞 ⇀ 𝒞        forest, edges c → c.supersedes
```

`supersede` is acyclic; each tree is one `(model_id, model_version)` and
versions increase along edges.

---

## 4. Integrity constraints

Numbered for citation. Those marked ⊨ are mechanically checked by
`tests/test_spec.py`; those marked ⊢ are enforced at runtime by the
implementation (DB trigger, API guard or pipeline code).

**I1 (descriptor conformance, ⊨).** ∀d ∈ 𝒟 : `d ⊧ scheme-descriptor.json`,
and `d.parameters.schema` is itself a valid JSON Schema 2020-12 document.

**I2 (adapter–descriptor agreement, ⊨).** For every installed adapter `α`
with descriptor `d`:
```
α.params_schema() = d.parameters.schema  ∧
α.default_params() = d.parameters.defaults ∧
α.capabilities.publishable_detector = d.key_model.publishable_detector ∧
α.capabilities.distortion_free = d.embedding.distortion_free ∧
α.scheme_version = d.version
```
*This is the constraint that prevents specification and implementation from
drifting apart; it is the reason descriptors are generated from adapters.*

**I3 (binding soundness, ⊨).** ∀d ∈ 𝒟, ∀b ∈ d.integrations :
```
b.status = supported  ⟹  b.stage = d.embedding.stage
```
A surface runs a scheme only if it exposes the stage the scheme embeds at.

**I4 (parameter validity, ⊢).** ∀c ∈ 𝒞 :
`c.scheme_params ⊧ scheme(c).parameters.schema`.

**I5 (carrier agreement, ⊢).** ∀c ∈ 𝒞 : `c.modality = scheme(c).modality`,
and if `c.carrier ≠ ⊥` then `c.carrier.unit = scheme(c).carrier.unit`.

**I6 (immutability, ⊢ DB trigger).** For any update `c → c′`:
```
c.status ≠ draft ⟹ πI(c) = πI(c′)
```
where `πI` projects the identity-defining attributes
{model_id, model_version, config_version, supersedes, scheme_id,
scheme_version, scheme_params, tokenizer_ref, key_ref}. Lifecycle, calibration,
detector and label attributes remain mutable.

**I7 (activation gate, ⊢).** `c.status = active ⟹ c.calibration_ref ≠ ⊥`
∧ `calib(c).status = accepted`. In the `provider` profile additionally
`calib(c).quality = standard`.

**I8 (purpose separation, ⊢).** ∀c ∈ 𝒞 : `key(c).purpose = generation`. No
code path signs with a generation key or marks with a signing key.

**I9 (marking window, ⊢).** A configuration may mark at time `t` iff
`c.status = active ∧ key(c).state = active ∧ (c.generate_until = ⊥ ∨ t < c.generate_until)`.
Verification carries no such condition: `verify_until = ⊥` means verify
forever, which is the default.

**I10 (rotation, ⊢).** Rotating key `k` to `k′` yields
`lineage(k′) = lineage(k)`, `k′.key_version = k.key_version + 1`,
`k.state := verify-only`, and for every `c` with `key(c) = k ∧ c.status = active`
a fresh `c′` with `c′.supersedes = c.config_id`, `c′.key_ref = k′.key_id`,
`c′.calibration_ref = c.calibration_ref`, `c.status := deprecated`.
*The calibration is inherited because the null distribution of the statistic
under human content does not depend on which key is scanned.*

**I11 (destruction, ⊢).** `k.state := destroyed` requires
`k.verify_until ≠ ⊥ ∧ k.verify_until < now`.

**I12 (chain integrity, ⊢).** ∀a ∈ 𝒜 ordered by `seq`:
```
a.prev_hash = (a.seq = 0 ? 0^64 : A[a.seq−1].entry_hash)
a.entry_hash = SHA256(canon(a ∖ {entry_hash, signature}))
a.action = anchor ∧ pk ≠ ⊥ ⟹ Verify_pk(a.signature, a.entry_hash)
```

**I13 (no text at rest, ⊢).** `dom(a.details) ∩ {text, prompt, completion,
key_material, secret, passphrase} = ∅`, and no stored entity has an attribute
whose value is submitted content. `V.text_digest` is
`HMAC(deployment_salt, normalize(content))` or `⊥`.

**I14 (declaration safety, ⊨).** For a declaration `P` projected from `c`:
no attribute of `P` holds a key reference, lineage id or key material; and
```
m.params ≠ ⊥  ⟺  c.labels[disclosure] = public-detector
m.spoofing_not_excluded = (c.labels[disclosure] = public-detector ∨ c.detector_ref ≠ ⊥)
m.config_fingerprint = "sha256:" · SHA256(canon⟨scheme_id, scheme_version,
                                           scheme_params, lineage, key_version⟩)
```

**I15 (honesty, ⊢).** For every result `V` (the predicates the product exists
to guarantee):
```
(a)  V has no attribute of type 𝔹 denoting a verdict; ⟦V⟧_𝔹 is undefined
(b)  V.band = insufficient_evidence ⟹ V.candidates = ε
(c)  V.band ≠ insufficient_evidence ⟹ ∀κ ∈ V.candidates : κ.ci95 ≠ ⊥
(d)  ∀κ : κ.p_adj = min(1, |V.candidates| · κ.p)
(e)  result_is_probabilistic ∈ V.caveats
(f)  V.band = no_evidence ⟹ V.band_reason contains the human-authorship
     disclaimer
(g)  fpr_target < floor(calib) ⟹ V.band ⪯ weak_evidence
```

---

## 5. Lifecycles as labelled transition systems

**Configuration** `(Status, →_C)`, with `activate` guarded by I7:

```
                 activate                deprecate              retire
   draft ───────────────────────► active ─────────► deprecated ────────► retired
     │                                                                      ▲
     └──────────────────────────────── retire ──────────────────────────────┘
```
`→_C` is exactly `{draft→active, draft→retired, active→deprecated,
deprecated→retired}`; `retired` is absorbing. Marking is enabled only in
`active`; **verification is enabled in every state**, which is why there is no
path that removes a configuration from the scan set.

**Key** `(KeyState, →_K)`:

```
   pending ──custody met──► active ──rotate──► verify-only ──┐
                              │                               │ verify_until
                              └──revoke──► revoked ───────────┴──► destroyed
```
`revoke` is available from `active` and `rotating`; `destroy` only from a
state whose `verify_until` has elapsed (I11). Both `verify-only` and `revoked`
retain verification capability, and results matching them carry
`historical_key`.

---

## 6. The verification judgment

The pipeline is a function
`𝒱erify : Content × ℘(𝒞) × Fpr → V`, defined as the composition

```
𝒱erify = band ∘ correct ∘ calibrate ∘ detect ∘ select ∘ gate
```

Written as inference rules (`Γ` is the deployment: registry, null models,
policy). Let `u(x)` be the unit count of content `x` under the candidate's
carrier.

```
                      u(x) < c.verification_defaults.min_tokens
  ─────────────────────────────────────────────────────────────── (Gate-Short)
     Γ ⊢ ⟨x, c⟩ ⇓ skip(short_text)


          tokenizer_hash(c) ≠ recorded_hash(c)      modality(x) = text
  ─────────────────────────────────────────────────────────────────── (Gate-Tok)
     Γ ⊢ ⟨x, c⟩ ⇓ skip(tokenizer_uncertain)


   c.status ∈ {active, deprecated}    c.calibration_ref ≠ ⊥    ¬skip
   s = α_c.detect(x)    scored(s) > 0    p = N_c(s, scored(s))
  ──────────────────────────────────────────────────────────────── (Score)
     Γ ⊢ ⟨x, c⟩ ⇓ κ with κ.p = p, κ.ci95 = boot_c(s)


        Γ ⊢ ⟨x, cᵢ⟩ ⇓ κᵢ   (i = 1..k)      k ≥ 1
        κ* = argmin κᵢ.p_adj      κᵢ.p_adj = min(1, k · κᵢ.p)
  ───────────────────────────────────────────────────────────── (Correct)
        Γ ⊢ x ⇓ ⟨band(κ*.p_adj, φ, floor), {κᵢ}⟩


                    ∀i : Γ ⊢ ⟨x, cᵢ⟩ ⇓ skip(_)
  ─────────────────────────────────────────────────────────────── (Insufficient)
        Γ ⊢ x ⇓ ⟨insufficient_evidence, ε⟩
```

with the band function, for target `φ` and calibration floor `ρ`:

```
band(p, φ, ρ) =
   weak_evidence      if φ < ρ  ∧  p ≤ 0.05        (capping, I15g)
   strong_evidence    if p ≤ φ/100
   moderate_evidence  if φ/100 < p ≤ φ
   weak_evidence      if φ < p ≤ 0.05
   no_evidence        if p > 0.05
```

Rule `(Insufficient)` is what makes `insufficient_evidence` incomparable in
the band order: it is reached by a *different derivation* than any banded
result, never by a small `p`.

---

## 7. Modality extension, formally

A modality is introduced by extending four vocabularies and supplying one
function. Given new `m ∈ X_mod` with unit `υ ∈ Unit` and codec `γ`, an
implementation must provide

```
count_υ : Content_m → ℕ
```

and nothing else: `select`, `correct`, `band`, key custody, audit and the
declaration projection are all parametric in the carrier. The statistics layer
requires only that the null model is fitted over `(statistic, size)` pairs in
the same unit `υ`, which `fit_null_by_length` already is.

The three shipped image descriptors (`docs/spec/examples/`) instantiate the
three image stages and both image units, which is the finite check that the
extension is total over the image case:

| descriptor | stage | unit | statistic | direction |
|---|---|---|---|---|
| `stable-signature` | `component_weights` | pixel | `bit_accuracy` | higher |
| `tree-ring` | `latent_init` | latent | `distance` | **lower** |
| `dwt-dct` | `posthoc` | pixel | `bit_accuracy` | higher |

`tree-ring` is the witness that `Dir` must exist: a distance statistic is
*lower* when marked, and only the tail chosen by `N_c` changes.

---

## 8. Mapping to the normative schemas

| § | Entity | File |
|---|---|---|
| 2.1 | `D` scheme descriptor | `schemas/scheme-descriptor.json` |
| 2.2 | `C` deployment manifest | `schemas/model-config.json` |
| 2.3 | `K` key record | `schemas/key-record.json` |
| 2.4 | `R` calibration run | (registry table; schema pending) |
| 2.5 | `V` verification result | `schemas/verification-result.json` |
| 2.6 | `A` audit entry | `schemas/audit-entry.json` |
| 2.7 | `P` repo declaration | `schemas/repo-declaration.json` |
