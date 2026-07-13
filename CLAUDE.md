# quints monorepo

uv workspace; the five distributions live in `packages/*` (see README for what each does).

## Quality gate

- `make check` — the whole gate: ruff, basedpyright, import-linter, deptry, vulture, pytest. CI and the Claude Code Stop hook run exactly this. Keep it green.
- `make static` — same minus tests. `make format` — apply ruff autofixes.

basedpyright runs strict with a committed baseline (`.basedpyright/baseline.json`) that pins ~500 pre-existing errors. New and edited code must type-check clean. Never run `make typebaseline` to bury new errors — only to re-pin after *fixing* baselined ones (use the `ratchet` skill).

## Architecture (enforced by import-linter)

`quints` is layered, declared in the root `pyproject.toml`:

1. `cli`, `fava`, `plugins` (entrypoints)
2. `settlement`, `report_pdf`, `match`, `importing` (orchestration)
3. `mwst`, `kmu`, `vat`, `fx`, `prices`, `inbox`, `receivables`, `invoice` (domain)
4. `config`, `ledger`, `ui` (foundation)

A module may only import from layers below its own, and every new module must be added to a layer (the contract is exhaustive — `lint-imports` fails until you place it). The `beangulp-*`/`beanprice-*` packages are standalone: they never import each other or `quints`.

## Conventions

- Annotate all new function signatures — strict basedpyright flags missing ones.
- Each package declares its own dependencies in its own `pyproject.toml`; deptry fails on undeclared or unused ones.
- The test suite runs in ~2s. Keep it that way — no network, no sleeps.
