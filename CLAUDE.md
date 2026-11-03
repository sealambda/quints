# quints monorepo

uv workspace; the distributions live in `packages/*` (see README for what each
does).

## Quality gate

- `make check` — the whole gate: ruff, basedpyright, import-linter, deptry,
  vulture, pytest. CI and the Claude Code Stop hook run exactly this. Keep it
  green.
- `make static` — same minus tests. `make format` — apply ruff autofixes.
- `nix flake check` — the Sealambda hub's checks for everything that isn't
  Python: actionlint, zizmor, shellcheck, shfmt, nixfmt, typos, and the synced
  tool configuration. `nix fmt` applies the fixes. typos rewrites in place: add
  foreign or deliberately misspelled words to `_typos.toml`, never let it
  "correct" a German quotation.

basedpyright runs strict with a committed baseline
(`.basedpyright/baseline.json`) that pins ~500 pre-existing errors. New and
edited code must type-check clean. Never run `make typebaseline` to bury new
errors — only to re-pin after *fixing* baselined ones (use the `ratchet`
skill).

## Architecture (enforced by import-linter)

`quints` is layered, declared in the root `pyproject.toml`:

1. `cli`, `fava`, `plugins` (entrypoints)
2. `settlement`, `report_pdf`, `match`, `importing`, `init`, `closing`,
   `liability` (orchestration)
3. `mwst`, `kmu`, `vat`, `fx`, `prices`, `inbox`, `payables`, `receivables`,
   `invoice` (domain)
4. `config`, `ledger`, `ui` (foundation)

A module may only import from layers below its own, and every new module must
be added to a layer (the contract is exhaustive — `lint-imports` fails until
you place it). Modules sharing a layer may import each other, but never in a
cycle. The `beangulp-*`/`beanprice-*` packages are standalone: they never
import each other or `quints`.

## Conventions

- Annotate all new function signatures — strict basedpyright flags missing
  ones.
- Each package declares its own dependencies in its own `pyproject.toml`;
  deptry fails on undeclared or unused ones.
- The test suite runs in ~2s. Keep it that way — no network, no sleeps.
- Docs (`docs/`): follow CONTRIBUTING.md § Writing docs. Pages are job-first
  steps from the company's point of view, open with an "applies if" block
  (legal form, VAT status, method, period), and cite sources as footnotes.
- The shared tool configuration (`.editorconfig`, `.markdownlint-cli2.yaml`,
  `.vale.ini`, `lychee.toml`) is synced from the hub and must stay identical:
  override per directory (`docs/.markdownlint-cli2.yaml`,
  `packages/.editorconfig`), or change it in sealambda/.github.
