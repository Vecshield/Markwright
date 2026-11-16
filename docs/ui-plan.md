# UI improvement plan

Two tracks. Track A is wording: audit and fix the AI-sounding copy across the
UI, CLI, API strings and README (done in the same change as this plan; the
style guide below is the lasting part). Track B is flows: concrete UX work,
ordered by value. The §8.3 rules (one hue, identical no/insufficient treatment,
caveats and the false-positive target always visible, intervals on every
p-value) stay fixed through all of it.

## Track A — copy style guide

Rules for every string a user can see (UI, CLI output, `band_reason`, caveat
texts, calibration notes, API descriptions, README):

1. **Say the thing, once.** Write what the system did or what the number means.
   Cut sentences that only add weight ("That is the point", "This matters").
2. **One corrective statement per surface.** "It does not decide whether text
   is AI-written" is required product language, and it lands harder when it
   appears once per screen instead of in every paragraph. Everywhere else,
   state the positive fact plainly instead of using the "X, not Y" template.
3. **No reader-facing jargon or abbreviations.** Spell out: false-positive
   target (not "FPR"/"fpr"), 95% interval (not "CI"), multiple-scan correction
   (readers who want "Bonferroni" find it in the API payload), "too
   predictable" alongside "low-entropy", "length and predictability check"
   instead of "gate". Internal identifiers (caveat keys, band values, field
   names) are wire contract and do not change.
4. **Numbers in plain language first.** A false-positive target renders as
   "1 in 10,000" with the scientific form in parentheses where precision
   matters. `one_in_n()` already exists for this; the UI reuses the idea.
5. **Punctuation:** no em dashes in product copy; use a period, comma, colon
   or parentheses. Straight quotes.
6. **Required sentences stay.** Two sentences are product rules and must
   appear verbatim or near-verbatim where results render: absence of a mark is
   not evidence of human authorship (on every no-evidence result), and the
   footer's responsible-use summary.

### Audit findings fixed in this change

| Where | Before | After |
|---|---|---|
| bands.py STRONG text | "...This is not proof, and not a statement about authorship." | sentence dropped; the always-on caveat already carries it |
| bands.py WEAK text | "below this deployment's bar" | "below this deployment's false-positive target. Too weak to act on alone." |
| bands.py INSUFFICIENT | "Too short or too low-entropy to test" | "Too short, or too predictable (code, lists, quotes), to test" |
| band_for reasons | "p <= fpr_target/100 (1.0e-06)" | "the calibrated p-value is below one hundredth of the false-positive target (1.0e-06)" |
| caveat `result_is_probabilistic` | "result is probabilistic, not proof" | "statistical evidence; some human texts score this high by chance, at the rate shown" |
| calibration note | "analytic p assumes token independence; use calibrated p" | "the analytic p-value assumes tokens are independent, which real text violates; trust the calibrated p-value" |
| header chip | "fpr 1e-2" | "false-positive target 1 in 100" |
| Overview pipeline step | "gate" / "Bonferroni" | "length check" / "multi-scan correction" |
| Overview | "intensity, not judgment" | "colour shows how strong the evidence is; it never means pass or fail" |
| quickstart closer | "That is the point." | "Short input never produces a p-value." |
| em dashes | 45+ across UI/README | replaced with periods, commas, colons |

## Track B — flows, ordered by value

### B1. Verify flow (the verifier's whole world)
- [x] **Example inputs**: three presets from `GET /v1/samples`, generated on the
  running deployment (marked with its own key, a human passage, a 30-token
  slice), each stating what to expect and why.
- [x] **Live input feedback**: token estimate with a warning below the minimum,
  so "insufficient evidence" is never a surprise.
- [x] **Submit with Cmd/Ctrl+Enter**; friendly message for malformed JSON.
- **Scope selector**: filter which configs are scanned (model family,
  time window of generation, scheme) once the result-count grows; fewer
  candidates also means a smaller multiple-scan correction, and the UI should
  say so.
- **Result permalink + copy button**: results are already stored; add
  GET-by-id and a copyable link so a verifier can hand a result to a reviewer.
- [x] **Readable samples**: presets load decoded prose, not token ids. Token ids
  appear only for the synthetic marked sample, which says why.
- [x] **Charts over tables**: evidence scale with a marker, p-value on a log
  axis against the target with the 95% interval drawn, token-length gauge, and
  a per-configuration dot plot. Raw numbers move behind a disclosure; the
  caveats and the target stay in flow.
- [x] **Network failure** reads "cannot reach the API, start it with ..." rather
  than a bare TypeError.
- **File drop**: drag a .txt onto the box.

### B2. Onboarding flow
- **Resumable**: persist wizard state in localStorage so a page reload does not
  restart it; offer "continue where you left off".
- **Inline validation**: model-id slug format checked as you type, with the
  pattern shown; tokenizer existence checked against the HF cache via a new
  lightweight API probe.
- **Progress honesty**: the calibrate step shows a determinate count
  (docs processed / total) via a streaming or polling endpoint instead of one
  long spinner.
- **End-of-wizard handoff**: show the exact hook command for the operator's
  stack (HF or vLLM), with a copy button, instead of a generic pointer.

### B3. Registry
- Search and status filters above the table once a deployment has more than a
  handful of configs; filter chips, not dropdowns.
- Config detail as a routable anchor (#registry/<id>) so links land on the
  open drawer.
- Show calibration age and flag stale calibrations (the design's 180-day
  expiry) with a plain sentence, no icon soup.

### B4. Keys
- **Validity timeline**: the §6 overlap diagram as a simple horizontal bar per
  lineage (generates-from → generates-until, verifies-forever tail). This is
  the single most explanatory visual the design asks for.
- Rotation preview: before confirming, list the configs that will be
  deprecated and superseded.

### B5. Admin
- Policy change preview: before saving a tighter target, show which recent
  results would have banded differently ("3 of the last 20 moderate results
  would have been weak"). Data is already in the results table.
- Audit export as a download button (the endpoint exists); show the last
  anchor-verified line.

### B6. Calibration screen (last missing §8.3 screen)
- Blocked on robustness-sweep data (backend: marked corpus + edit fractions).
  When that lands: null-statistic distribution per bucket, detection-rate vs
  length, detection-rate vs edit fraction, min-detectable-length history.
  Recharts or plain SVG; keep the one-hue rule.

### B7. Platform polish
- [x] Role changes refetch every screen (App keys screens by the auth token).
- [x] Permission limits render as a calm notice, never an error banner.
- [x] Toasts for completed actions (policy save, key rotate/revoke).
- Loading skeletons for tables; empty states already exist, keep them wordy.
- Toast for completed actions (rotate, policy save) instead of inline-only.
- `prefers-color-scheme` picks the default theme; toggle still overrides.
- Error boundary around screens; show the request id for API 500s.
- Keyboard focus ring audit (focus-visible exists); aria-labels on band chips
  ("moderate evidence, 1 in 300 by chance"); contrast check for weak-band text.
- Component extraction (Table, Facts, Banner) once a third screen needs them,
  not before.
- Playwright smoke: wizard end-to-end + verify both outcomes, run in CI
  against the dev API.

### Explicitly out of scope for the dashboard
Multi-tenant admin, user management (OIDC owns identity), charts for their own
sake, and any red/green status color anywhere.
