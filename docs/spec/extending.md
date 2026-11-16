# Extending OWM: new schemes, new modalities, new serving surfaces

Three walkthroughs, each anchored to working code or a shipped exemplar.

## 1. Adding a text scheme

KGW is the worked example; `packages/markwright-scheme-kgw` is ~200 lines and
the core never learned it exists (`tests/test_kgw.py` asserts
`"kgw" not in schemes._BUILTIN`).

1. **Write the descriptor first**: `<scheme_id>.owm.json`, validating against
   `schemas/scheme-descriptor.json`. Forcing the descriptor first surfaces the
   operational questions before any code: what is the statistic, does
   detection need the key, can a detector be published, which stage does the
   mark attach at.
2. **Implement the adapter**: the `SchemeAdapter` protocol from
   `markwright.schemes.base` (`params_schema`, `default_params`,
   `generate_config`, `apply`, `detect`, `calibrate`, `export_detector`).
   `detect` must accept pre-tokenized unit sequences, not only raw content;
   that is what makes calibration, offline verification and deterministic
   tests possible without hosting a model.
3. **Ship both in one package**: the descriptor file sits next to the module;
   the entry point registers the adapter:
   ```toml
   [project.entry-points."markwright.schemes"]
   my-scheme = "my_package:MyAdapter"
   ```
4. **Honesty invariants**: `export_detector` returns None when the detector
   cannot be separated from the key; never propagate boolean verdict fields
   from wrapped libraries; declare `guidance: calibrate` unless the analytic
   p-value holds on real text, which it almost never does.
5. The conformance tests do the rest: descriptor validity, adapter-descriptor
   equality, binding-rule consistency.

## 2. Adding a modality (the image walkthrough)

The spec ships three specification-only image descriptors in
`docs/spec/examples/`, chosen because together they exercise every image
stage:

| Descriptor | Stage | Unit | Statistic | Detection needs |
|---|---|---|---|---|
| `stable-signature` | `component_weights` | pixel | `bit_accuracy` | a trained extractor |
| `tree-ring` | `latent_init` | latent | `distance` (lower is marked) | the model itself (DDIM inversion) |
| `dwt-dct` | `posthoc` | pixel | `bit_accuracy` | the key |

What the data model already covers, with the exemplars as proof:

- **Carrier**: unit `pixel` or `latent`, raster or latent-space codec,
  resolution constraints, and a `min_units_default` that plays the role the
  token minimum plays for text. The gate rule is unchanged: below the minimum,
  insufficient evidence, no statistic.
- **Stage**: `component_weights` has no per-request hook at all (the mark
  lives in fine-tuned VAE weights), which the binding model expresses as a
  `hf-diffusers.component-swap` surface. `latent_init` maps to supplying
  key-seeded latents through `hf-diffusers.pipeline-callback`. `posthoc` runs
  anywhere.
- **Statistic direction**: Tree-Ring is the reason `direction` exists;
  its distance statistic is lower when marked, and the null-model machinery
  only needs to know which tail to integrate.
- **Artifacts as identity**: for Stable Signature the fine-tuned decoder
  fingerprint belongs in the deployment manifest's artifacts exactly where the
  tokenizer hash lives for text, and a swap must fail verification the same
  way a tokenizer mismatch does.

What an implementation adds (deliberately not shipped here):

1. An `ImageContent` type alongside `str | TokenIds`, and a carrier-aware
   unit counter replacing the tokenizer-based one.
2. Null-model buckets keyed by resolution bucket instead of token-length
   bucket. `fit_null_by_length` already takes (statistic, size) pairs; only
   the bucketing table changes.
3. Null corpora of human/unmarked images with the same provenance honesty as
   the bundled text corpora: documented source, provisional grade, resolution
   floor.
4. Calibration of `bit_accuracy` statistics against the binomial analytic
   value, which exists but is still re-mapped through the fitted null,
   because JPEG artifacts and crops violate the independence assumption just
   as surely as real text violates token independence.

A new modality beyond image follows the same recipe: register (or `x-` prefix)
the modality, its units, its stages and its surfaces; write one
specification-only descriptor per candidate scheme; make the conformance suite
pass; only then write adapters.

## 3. Adding a serving surface

A surface is one place in one stack where a stage is exposed.

1. If the stack exposes an already-registered stage, the work is one
   `integrations` entry in each relevant descriptor, plus the adapter that
   performs the binding. The vLLM wrapper is the template:
   `markwright.inference:VLLMLogitsProcessor` adapts the HF batch convention
   to vLLM's per-request convention in ~30 lines, and the simulated-sampler
   test proves equality with the HF path.
2. If the surface is new, propose it for the registered list or ship it as
   `x-yourorg.surface` until adopted. Registered surfaces are added by a spec
   revision, not by editing a deployed schema.
3. Surfaces never appear in results or manifests; they matter at generation
   time only. A verifier needs the carrier and the scheme, never the stack
   that produced the content. That separation is what lets one verification
   path cover every serving framework.

## 4. Versioning rules

- Documents carry `owm: "0.1"`. Additive fields may appear within a minor
  version; anything that changes meaning bumps it.
- Descriptors version independently of the spec (`version`, semver). A
  parameter-schema change is a new descriptor version and therefore a new
  configuration version in any registry that adopts it; old configurations
  keep verifying under the descriptor version they were created with.
- Unknown `x-` values must round-trip: consumers preserve them, never strip.
