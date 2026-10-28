# Bundled starter null corpora

These exist so the POC pipeline runs end to end. They are **provisional-grade
by construction** and every null model fitted on them says so
(`quality: provisional` → `provisional_calibration` caveat on every result,
resolution floor enforced by `band_for`).

| corpus id | contents | passages | buckets | floor |
|---|---|---|---|---|
| `gutenberg-en-v1` | 4 public-domain novels (Austen, Melville, Doyle, Dickens), pre-tokenized **gpt2** ids | 900 | ~300 each near 100/200/400 tokens | ≈ 3.3e-3 |

Format: JSONL, one passage per line: `{"id", "source", "n", "ids"}`. Token ids,
not text — calibration runs through the adapter's `TokenIds` detect path, so it
needs no tokenizer and no network.

Known limitations (why this is provisional):
- Single domain and register: 19th-century English fiction. A deployment
  verifying e.g. news or code against this null model has an unmeasured domain
  shift.
- 300 docs/bucket → empirical resolution ~1/301 ≈ 3.3e-3; no GPD tail fits at
  this size, so there is no extrapolation below the empirical floor. An FPR
  target of 1e-4 **cannot be resolved** — the dev profile defaults to 1e-2.
- gpt2 tokenization only. A config with a different `tokenizer_ref` must not
  calibrate on this corpus (the calibration API refuses).

Production guidance (design §7.2): calibrate on a documented, versioned sample
of human text from your own domain — thousands of documents per length bucket —
and record corpus ids and hashes in the calibration report.

Rebuild: `.venv/bin/python scripts/build_null_corpus.py` (network; never run by
the API or tests).
