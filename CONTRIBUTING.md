# Contributing to quints

Thanks for helping out. This is a [uv](https://docs.astral.sh/uv/) workspace:
the distributions live under `packages/*`, and `quints` itself is composed
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
2. `settlement`, `report_pdf`, `match`, `importing`, `init`, `closing`,
   `liability` — orchestration
3. `mwst`, `kmu`, `vat`, `fx`, `prices`, `inbox`, `payables`, `receivables`,
   `invoice` — domain
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

## Writing docs

The docs site (`docs/`) is read by whoever keeps a company's books: a
founder, a freelancer, a Treuhänder, or an AI agent working for one of them.
They come with a job to do ("file the H2 return", "we registered for VAT in
March", "close 2026"), not with questions about how quints works inside. Write
for that job.

### Page shape

1. **The title names the job.** "File your VAT return", not "The MWST module".
2. **"Applies if" comes first.** Say which situations the page covers and
   which it doesn't. These are the axes that change Swiss bookkeeping:
   - legal form: Einzelfirma / GmbH / AG
   - VAT status: not liable / registering / registered / deregistering
   - VAT method: effective / Saldosteuersatz
   - filing period: quarter / half-year / year

   For each axis, say what changes, or say that nothing does. Never assume a
   GmbH, the effective method, quarterly filing, or a company that was
   VAT-registered from day one.
3. **Then numbered steps, in the order the user does them.** Each step gives
   the command, what to check in its output, and what to paste or decide
   next. A decision is a step too ("register or not", "which method"): give
   the criteria, and cite them.
4. **Show variants as content tabs, with the same labels on every page**:
   `Not VAT-registered` / `Effective method` / `Saldosteuersatz`, and
   `Einzelfirma` / `GmbH` / `AG`. `content.tabs.link` is on, so a reader
   picks once and every page follows.
5. **Add a "What quints doesn't do here" section.** List each gap with its
   manual workaround. For example, quints doesn't value the goods on hand at
   registration, so the page says to book the Einlageentsteuerung by hand,
   tagged `mwst: "einlageentsteuerung"`.
   Link the issue if one exists. A gap the docs admit to is a gap an agent
   can work around. A hidden one becomes a wrong return.
6. **Put the internals last.** Classification rules, algorithms and edge
   cases go in a "How it works" section at the bottom, a collapsed
   `??? info` block, or a Reference page. Keep them: an agent needs the rules
   to predict a result. They just aren't the first thing a reader needs.
7. **End with references**, as footnotes (see below).

### References

Every legal or regulatory claim gets a source the reader can check. A human
or an agent should be able to decide whether a rule applies to *their* case
without taking quints' word for it.

- Law: act, article and fedlex link, e.g. Art. 37 MWSTG (SR 641.20).
  Ordinances the same way (MWSTV, SR 641.201).
- ESTV publications: the MWST-Info or MWST-Branchen-Info number, title and
  section, with its *Stand* date (e.g. MWST-Info 12 *Saldosteuersätze*,
  Ziff. 2.2.3). Link the section, not only the table of contents.
- Standards: the document and its version (e.g. SIX Swiss Implementation
  Guidelines QR-bill 2.3).
- Cite the current text, not memory. When a rule changed (the 2024 rates,
  the 2025 MWSTG revision), give both validity dates.
- When quints encodes a rule, such as a rate table or a threshold, say where
  it lives in the code as well as where it comes from.

Use Markdown footnotes (`[^mwstg-37]`, defined at the end of the page). Keep
the body readable and the sources complete. `guides/payment-references.md`
is the model.

### Lifecycle, not snapshot

A company's situation changes over its life. It often starts below the
registration threshold and registers later, when it crosses the threshold or
chooses to. It may switch between the effective method and the
Saldosteuersatz, move to annual filing, or deregister. For every feature you
document, ask what happens before registration, in the period the change
takes effect, and after it. If quints handles it, document the steps. If it
doesn't, say so under "What quints doesn't do here" and open an issue. Don't
write a page that only works for a company that never changed.

### Mechanics

- Voice: lead with the command or the fact, in short declarative sentences.
  No marketing, and no "simply", "just" or "powerful".
- Every documented command runs in CI (`packages/quints/tests/test_docs.py`).
  Each page runs as one session in page order, tabs included, so a tab that
  scaffolds its own project must `cd` back out afterwards. Mark a block
  `<!-- no-test -->` only when it needs network access or credentials.
- This guidance lives here, not in `docs/`: everything under `docs/` is
  published, and every `quints` line in its `bash` fences is executed.

## Pull requests

- Branch off `main`; keep `make check` green.
- `packages/quints/examples/` depends on the *released* `quints`, like any
  scaffolded project: `uv run quints …` inside it, or a `quints` installed with
  `uv tool`/`pipx`, does not exercise your checkout. From inside `examples/`
  use `uv run --project ../.. quints …`; from the repo root, `uv run quints …`.
- If you document a command, run it against `packages/quints/examples/` first.
  The docs test (`test_docs.py`) executes the commands in the README and every
  page of `docs/`, fenced blocks inside tabs and admonitions included, so they
  can't silently rot.
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
