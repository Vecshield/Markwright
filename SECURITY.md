# Security policy

## Reporting

Report vulnerabilities privately via **GitHub Security Advisories** on this
repository (Security → Report a vulnerability). We acknowledge within 3
business days and aim to ship a fix or mitigation within 30 days for anything
rated high or critical. Coordinated disclosure: please give us that window
before publishing.

## What counts as severe here

Markwright's security promises are specific, so severity is too:

- **P0 — key material escaping the backend boundary.** Secret bytes in logs,
  audit entries, API responses, error messages, result payloads, or on disk
  outside a KeyBackend. The design stores handles, never secrets; a
  reproduction showing otherwise is the most serious report possible.
- **P0 — submitted text being stored.** Anything beyond the salted digest and
  statistics (including in audit details, which structurally reject
  text/prompt/secret keys).
- **P1 — audit-chain integrity.** Forging, reordering or truncating entries in
  a way `markwright audit verify` accepts; evidence bundles that validate after
  tampering.
- **P1 — honesty-rule bypass.** Any path that returns a boolean verdict, an
  uncalibrated p-value presented as calibrated, or a band finer than the
  calibration's resolution floor.
- **P2 — RBAC/OIDC bypass**, cross-role data exposure (e.g. verifiers reading
  scheme parameters or key refs), break-glass working in the provider profile.

## Out of scope

Watermark *removal* (paraphrase, translation, re-sampling) is a documented
limitation of every scheme we wrap, not a vulnerability in Markwright.
Spoofing text under a *published* detector is likewise documented and labelled
(`spoofing_not_excluded`) — but spoofing under an **unpublished** key would be
a real finding.

## Supported versions

Pre-1.0: only the latest `main` receives fixes.
