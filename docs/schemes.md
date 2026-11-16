# Watermarking schemes: what they do and how they are onboarded

Markwright operates published schemes and writes none of its own. This page is
the reference behind the dashboard's Schemes screen and `GET /v1/schemes/catalogue`;
both read the same source, `src/markwright/schemes/explain.py`.

## How a scheme becomes a live configuration

```
1. register        2. configure          3. key                4. calibrate
model + version    scheme parameters     created in the        score a human corpus,
+ tokenizer        validated by the      key backend;          fit the null model
                   scheme's own schema   registry keeps a      (ECDF + GPD tail)
                                         reference only
        │                   │                    │                      │
        └───────────────────┴────────────────────┴──────────────────────┘
                                      │
                    5. accept                     6. activate
                    an operator signs off on      marking starts;
                    the run; the tokenizer        verification begins and
                    fingerprint is recorded       continues after rotation
```

Two rules this path enforces:

- A configuration cannot be activated until a calibration is accepted. Without a
  null model there is no p-value, only a raw score.
- Rotating a key later creates a new configuration version on the new key. The
  old version stops marking and keeps verifying old text indefinitely.

## Installed schemes

### SynthID-Text (Dathathri et al., Nature 2024)

Marks by running a tournament between candidate tokens at each sampling step.
The winner of each round is decided by a pseudo-random g-value keyed to the
preceding n-gram, so marked text wins more of those tournaments than chance
would explain. Quality holds up because every candidate was already an
acceptable next token.

Detection recomputes the g-values for the same n-grams and averages them over
the tokens that count; repeated contexts are skipped. Unmarked text averages
about 0.5 and marked text sits measurably above it. The statistic is the mean
g-value, and Markwright re-maps it through the calibrated null model rather than
reporting the adapter's analytic p-value.

Key material is 30 derived integers, computed inside the key backend from the
root secret. Detection needs the token ids and those integers, never a model
forward pass.

| Parameter | Controls |
|---|---|
| `ngram_len` | context window seeding each tournament (published default: 5) |
| `num_keys` | tournament depth; more layers means a stronger per-token signal |
| `detector` | `mean` (default) or a trained `bayesian` detector |
| `sampling` | temperature / top-k / top-p, recorded so a run is reproducible |

Trade-offs: not distortion-free, so the sampling distribution does change.
A detector can be published for third parties, but doing so discloses the key
list, and every result under that configuration then carries
`spoofing_not_excluded` permanently.

### KGW green-list (Kirchenbauer et al., ICML 2023)

Ships as a separate package, `packages/markwright-scheme-kgw`, which is also the
worked example for adding any scheme.

A seed derived from the preceding token splits the vocabulary into a green list
(a `gamma` fraction) and the rest; the sampler adds a fixed bias `delta` to every
green token's logit. Detection recomputes the green list per position and counts
how many tokens landed in it. Under no watermark the share is `gamma`, and the
z-score measures the excess.

| Parameter | Controls |
|---|---|
| `gamma` | fraction of the vocabulary on the green list (0.25 default) |
| `delta` | logit bias on green tokens; the quality/detectability dial |
| `seeding_scheme` | how the seed comes from context (`lefthash`, `selfhash`) |
| `vocab_size` | must match the tokenizer, since lists are per-vocabulary |

Trade-offs: the bias changes word choice and the cost grows with `delta`. There
is no publishable detector, because the green list *is* the key; third-party
verification needs a detect-only service or a dedicated verification-key
lineage. Markwright enforces this: `export_detector()` returns `None` and the
publish route answers 409 with the reason.

## Schemes described but not installed

`unigram` (Zhao et al.) and `kuditipudi-exp` (EXP/ITS) are in the catalogue with
the same fields, so an operator can compare them before deciding to add one.
Unigram uses one global green list, which survives editing better but is
trivially spoofable once leaked. The Kuditipudi family is distortion-free, with
detection by permutation test, which costs far more compute.

## Adding a scheme

A scheme is a package, never a core change:

1. Implement the `SchemeAdapter` protocol from `markwright.schemes.base`:
   `params_schema`, `default_params`, `generate_config`, `apply`, `detect`,
   `calibrate`, `export_detector`.
2. Declare the honest `Capabilities`, especially `publishable_detector` and
   `distortion_free`. The wizard and the API read them and will refuse
   operations the scheme cannot support.
3. Register the entry point:
   ```toml
   [project.entry-points."markwright.schemes"]
   my-scheme = "my_package:MyAdapter"
   ```
4. Add an entry to `EXPLANATIONS` so the catalogue can describe it.
5. `pip install` the package. The wizard lists it, the registry validates its
   parameters against its own schema, and the verify pipeline treats its
   statistic like any other.

The architecture test in `tests/test_kgw.py` asserts the core never learned
about KGW: `"kgw" not in schemes._BUILTIN`.
