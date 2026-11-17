# Contributing to Markwright

Thanks for considering it. This page is the practical version; the full working
contract (coding rules, honesty rules, how sessions are organized) is
[CLAUDE.md](CLAUDE.md), and it applies to humans too.

## Dev setup

```bash
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -e ".[dev,synthid]" -e packages/markwright-scheme-kgw
.venv/bin/pytest -q -m "not network"     # tiers 1+2, ~1 minute
./scripts/demo.sh                        # the end-to-end loop, self-checking
```

Python ≥ 3.11. Torch/transformers are only needed for the `synthid` extra and
tier-2 tests — the core, API, stats and tier-1 tests run without them.

For dashboard changes:

```bash
cd dashboard
npm ci
npm test                               # verification UI state regressions
npm run build                          # TypeScript + production bundle
```

## The three test tiers

| Tier | Marker | Needs | Runs |
|---|---|---|---|
| 1 | (none) | core deps only | every change — **blocks commit** |
| 2 | `torch` | torch+transformers; no network, no model weights (token-id fixtures) | every change when torch is installed |
| 3 / infra | `network`, `slow` | OpenBao / PostgreSQL containers, or a small model download | locally + dedicated CI jobs |

Infra containers, when you want the `network` tests locally:

```bash
docker run -d --rm --name mw-openbao -p 8200:8200 -e BAO_DEV_ROOT_TOKEN_ID=dev-root openbao/openbao:2
docker run -d --rm --name mw-postgres -p 5433:5432 -e POSTGRES_USER=markwright \
  -e POSTGRES_PASSWORD=test -e POSTGRES_DB=markwright postgres:16-alpine
.venv/bin/pytest -q -m ""                # everything
```

## Rules that will come up in review

- **Honesty rules are load-bearing.** No boolean result fields, no uncalibrated
  p-values, no red/green verdict UI, text never stored. Each has a test; a PR
  that weakens one needs a very good argument and a design-doc discrepancy note.
- **Schemas are the wire contract.** Change `schemas/*.json` and the code in the
  same commit; tier-1 validates responses against them.
- **Fixtures are regenerable artifacts.** Never hand-edit `tests/fixtures/`;
  change `tests/fixtures/regenerate.py` and rerun it.
- **Mocks replay reality.** Test adapters either replay recorded real detections
  (`ReplayAdapter`) or genuinely compute (`ParityAdapter`). A stub returning an
  invented constant will be asked to leave.
- **New schemes are new packages**, registered via the `markwright.schemes`
  entry point — never core edits. `packages/markwright-scheme-kgw` is the
  template.
- **Statistics come from numpy/scipy standard estimators; crypto comes from
  `cryptography`.** Nothing hand-rolled, ever.

## Workflow

Work is phased: [docs/poc-plan.md](docs/poc-plan.md) holds the plan,
[docs/IMPLEMENTATION_LOG.md](docs/IMPLEMENTATION_LOG.md) is the append-only
record (what changed, why, how verified, what was deferred). One commit per
coherent step, message prefixed with the phase id. Tier-1 green before every
commit; design-doc discrepancies get recorded, not silently resolved.

Contributions are accepted under the Apache-2.0 licence with DCO sign-off
(`git commit -s`).
