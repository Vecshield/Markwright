# OWM 0.1: Open Watermark Manifests

A data model for declaring, operating and verifying generative-media watermarks,
across schemes, serving stacks and modalities. Markwright is the reference
implementation; the format is independent of it.

Status: draft 0.1. The JSON Schemas under `schemas/` are normative; this
document explains them. Breaking changes bump the `owm` version string carried
by every document.

## 1. Why a standard

Watermarking schemes are published as papers and reference code. What a scheme
needs operationally never travels with them: where in the generation pipeline
the mark attaches, what key material it derives, what its detector measures,
what breaks it, and how a third party verifies a claim years later. Every
deployment re-answers those questions privately, and no two answers
interoperate.

OWM fixes the *shape* of those answers. Three documents cover three parties:

| Document | Published by | Answers |
|---|---|---|
| **Scheme descriptor** | the scheme author | what the scheme is and how to run it |
| **Deployment manifest** | the operator's registry | what was marked, with which parameters and key |
| **Repo declaration** | the model publisher | "outputs of this model are marked; verify like this" |

A fourth document, the **verification result**, already exists in most
implementations; OWM only fixes its honesty requirements (section 6).

## 2. Design rules

1. **The carrier is the extension point, not the scheme.** Text and image
   watermarks differ in what the mark lives in (tokens vs pixels or latents)
   and where it attaches. Everything downstream is shared: null models,
   calibrated p-values, evidence bands, multiple-scan correction, key custody,
   audit. OWM therefore models the carrier explicitly and keeps the statistics
   layer modality-free.
2. **Bindings are computable.** Serving frameworks expose *stages* (vLLM
   exposes logits; diffusers exposes initial latents and component weights;
   every stack supports post-hoc transforms). Schemes *require* a stage.
   Whether framework F can run scheme S is a lookup, never a wiki page.
3. **Vocabularies are closed but extensible.** Modalities, units, stages and
   surfaces come from registered lists; anything else must carry an `x-`
   prefix. A validator can tell "new, declared extension" from "typo".
4. **Public documents are public-safe by construction.** The repo declaration
   schema has no field that could hold a key reference or sealed material,
   and parameters appear only under an explicitly public disclosure.
5. **Declared is not measured.** Descriptors state what the literature claims
   (robustness, analytic p-values). Measured behaviour lives in calibration
   reports produced by the deployment, and implementations must calibrate
   rather than trust analytic values (the `guidance: calibrate` field).

## 3. The scheme descriptor

Normative schema: `schemas/scheme-descriptor.json`. One document per
(scheme_id, version), shipped as `<scheme_id>.owm.json` inside the scheme's
package, or standalone with `status: specification-only` for schemes that have
no adapter yet.

The load-bearing fields:

- `modality` plus `carrier` declare what content the mark lives in and the
  unit everything is counted in. The unit denominates the length gate
  (`min_units_default`), the scored counts in results, and the size buckets of
  null models. For text the unit is `token` and the codec is a tokenizer whose
  fingerprint implementations must record; for images it is `pixel`, `patch`
  or `latent`.
- `embedding.stage` is the single field that determines serving-framework
  compatibility. Registered stages: `logits`, `sampling`, `posthoc` (text);
  `latent_init`, `component_weights`, `posthoc` (image).
- `key_model` states whether detection needs the key, whether a
  stranger-runnable detector can exist, whether the backend can keep the key
  entirely inside a KMS (`backend_mac_supported`), and how material derives
  from a root secret.
- `detection.statistic` names and types the statistic
  (`mean`, `z_score`, `bit_accuracy`, `distance`, ...) with its direction, so
  a generic calibration layer can fit a null model without knowing the scheme.
- `parameters.schema` is a JSON Schema; registries validate configurations
  against it. This is the same schema the adapter exposes in code, and the
  conformance tests require the two to be identical.
- `integrations` lists bindings: `(surface, stage, status, adapter)`.

### Registered surfaces

`hf-transformers.generate`, `hf-transformers.logits-processor`,
`vllm.logits-processor`, `sglang.logits-processor`, `tgi.sidecar`,
`llama-cpp.sampler-hook`, `openai-proxy`, `hf-diffusers.pipeline-callback`,
`hf-diffusers.component-swap`, `posthoc.transform`.

The binding rule: a surface can run a scheme if and only if the binding's
stage equals the scheme's embedding stage. `posthoc.transform` is universal by
construction, which is why post-hoc schemes work on any stack and also why
they are the easiest to strip.

## 4. The deployment manifest

Normative schema: `schemas/model-config.json`. One row per
(model, model_version, config_version), immutable once active. OWM 0.1 opened
the scheme identifier (any descriptor-backed id, dotted namespaces for third
parties) and added `modality` and an optional `carrier` block; `tokenizer_ref`
remains the compact text-mode form. For an image configuration the carrier
records the codec and constraints, and `artifacts` fingerprints the marked
components; for Stable-Signature-style schemes the fine-tuned VAE fingerprint
is as identity-defining as a tokenizer hash is for text.

## 5. The repo declaration

Normative schema: `schemas/repo-declaration.json`. Conventional filename:
`watermark.owm.json`, placed at the root of the model repository (Hugging Face
or elsewhere), next to the model card.

It carries: the subject URI and artifact fingerprints; one `marking` entry per
configuration (scheme id, a `config_fingerprint` committing the publisher to
exact parameters and key lineage without disclosing them, active dates); the
verification route (published detector, detect-only endpoint, or offline
bundle); and `spoofing_not_excluded`, which MUST be true whenever a detector
or the parameters are public. The whole document may be signed by the
deployment identity; the signature covers the canonical JSON with the
signature fields removed.

Markwright generates and signs these: `GET /v1/configs/{id}/declaration` or
`markwright declaration export <config_id>`.

## 6. Verification results

Normative schema: `schemas/verification-result.json`. OWM adds the
modality-neutral `units {unit, total, scored}` block; `token_count` and
`scored_token_count` remain as deprecated text-mode aliases.

The honesty requirements are part of the spec, not implementation taste:
no boolean verdict fields; a length/predictability gate before any statistic;
calibrated p-values with intervals and the deployment's false-positive target
on every result; multiple-scan correction; capped bands when calibration
cannot resolve the target; and the fixed sentence that absence of a mark is
not evidence of human authorship.

## 7. Conformance

An implementation is OWM-conformant at a level:

- **Level 1, descriptor consumer**: parses descriptors, validates
  configurations against their parameter schemas, computes bindings.
- **Level 2, registry**: level 1 plus deployment manifests with immutability
  and lifecycle, key handles never containing secrets, and calibration-gated
  activation.
- **Level 3, verifier**: level 2 plus results meeting section 6, and repo
  declarations it emits validating and signing as in section 5.

The conformance suite is `tests/test_spec.py`; the adapter-descriptor
consistency test is the one that keeps code and spec from drifting.

## 8. Security considerations

A descriptor is a claim by its author; validate it, but trust robustness
declarations only as literature pointers. A repo declaration proves intent to
mark, never that a given output is marked; only verification does that. A
signed declaration pins the document to a deployment identity, not to truth.
Publishing a detector is irreversible disclosure for schemes whose detector
embeds key material, and every document that records such publication must
carry the spoofing flag from that moment on.
