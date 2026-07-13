# Contributing to quints

Thanks for helping out. This is a [uv](https://docs.astral.sh/uv/) workspace:
the five distributions live under `packages/*`, and `quints` itself is composed
from the standalone `beangulp-*` / `beanprice-*` building blocks.

## Setup

```bash
uv sync          # installs every workspace package, editable, into .venv
uv run quints --help
```

## The quality gate

One command runs everything — CI and the local pre-finish hook both run exactly
it, so keep it green:

```bash
make check       # ruff, basedpyright, import-linter, deptry, vulture, pytest
```

Useful subsets:

```bash
make static      # the gate minus tests
make format      # apply ruff autofixes (format + safe lint fixes)
make test        # uv run pytest packages -q
```

What each tool guards:

- **ruff** — formatting and lint.
- **basedpyright** (strict) — types. A committed baseline
  (`.basedpyright/baseline.json`) pins pre-existing errors; **new and edited
  code must type-check clean**. Annotate every new function signature. Don't run
  `make typebaseline` to bury new errors — only to re-pin after *fixing*
  baselined ones.
- **import-linter** — the architecture (see below).
- **deptry** — each package declares its own dependencies in its own
  `pyproject.toml`; undeclared or unused ones fail.
- **vulture** — dead code.
- **pytest** — the suite runs in ~2s. Keep it that way: no network, no sleeps.
  Fixtures and the example project stand in for live services.

## Architecture

`quints` is layered, declared in the root `pyproject.toml` and enforced by
import-linter. A module may only import from layers below its own:

1. `cli`, `fava`, `plugins` — entrypoints
2. `settlement`, `report_pdf`, `match`, `importing`, `init` — orchestration
3. `mwst`, `kmu`, `vat`, `fx`, `prices`, `inbox`, `receivables`, `invoice` — domain
4. `config`, `ledger`, `ui` — foundation

The contract is exhaustive: a new module must be placed in a layer or the gate
fails. Command logic stays presentation-free — a `compute` returns dataclasses,
a `render` turns them into output — which is what keeps the JSON output and any
future UI cheap. The `beangulp-*` / `beanprice-*` packages are standalone: they
never import each other or `quints`.

## Adding things

- **A reusable, entity-agnostic piece** → a new `packages/<name>/` workspace
  member with its own `pyproject.toml`, tests, and README (`beanprice-bazg` is
  the template).
- **A new `quints` module** → place it in a layer (above) and add its
  dependencies to `packages/quints/pyproject.toml`.
- **A CLI command** → keep compute and render separate, and add `--json`
  (`dataclasses.asdict`) — machine-readable output is the contract agents and
  tests rely on.

## Pull requests

- Branch off `main`; keep `make check` green.
- If you document a command, run it against `packages/quints/examples/` first —
  the docs test (`test_docs.py`) executes the README's commands, so they can't
  silently rot.
- VAT rates are law: they live date-ranged in `quints.ledger.VAT_RATES`, never
  in config.

## Releases

Only `quints` is published to PyPI. To cut a release: bump `version` in
`packages/quints/pyproject.toml`, then push a matching tag:

```bash
git tag v0.1.0 && git push origin v0.1.0
```

`.github/workflows/publish.yaml` runs the quality gate, builds `quints`, and
publishes it via PyPI trusted publishing (OIDC — no token), then creates a
signed GitHub release. The `beangulp-*` / `beanprice-*` building blocks are not
published; they live here in the workspace.

By contributing you agree your work is licensed under GPL-2.0-only (see
[LICENSE](LICENSE)).
