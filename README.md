# quints

[![CI](https://github.com/sealambda/quints/actions/workflows/ci.yml/badge.svg)](https://github.com/sealambda/quints/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/quints)](https://pypi.org/project/quints/)
[![Docs](https://img.shields.io/badge/docs-sealambda.github.io%2Fquints-blue)](https://sealambda.github.io/quints/)

Swiss bookkeeping as plain text: VAT (MWST) returns, QR-bill invoices and
statutory statements for a GmbH, AG or Einzelfirma, on
[beancount](https://github.com/beancount/beancount) and
[Fava](https://github.com/beancount/fava).

quints is a deterministic CLI with machine-readable output, built to be
driven by an AI coding agent (Claude Code, Codex, …). The agent proposes
bookings; quints checks them and computes every figure you file. It never
calls a model.

quints is not professional tax advice. You, or your Treuhänder, stay
responsible for what you file and sign.

![quints init scaffolds sample books, quints check validates them, and quints vat report prints the Form-310 VAT return](docs/assets/quickstart.gif)

**Docs: [sealambda.github.io/quints](https://sealambda.github.io/quints/).**
Every command shown there runs in the test suite.

## Install

```bash
uv tool install quints        # or: pipx install quints  /  pip install quints
```

Python 3.11+ (uv installs it if missing). Typst, for the PDFs, is bundled.

## Quick start

Sample books, with a quarter already booked:

```bash
quints init my-books --samples --yes
cd my-books
quints check
quints vat report -p 2026-Q3
```

For real books, paste this into Claude Code, Codex or another coding agent:

<!-- agent-prompt -->
```text
Set up my Swiss bookkeeping with quints. Its docs, as Markdown for you:
https://sealambda.github.io/quints/llms.txt

1. Install it: `uv tool install quints` (or `pipx install quints`).
2. Run `quints init --questions --json`. It lists what quints must know
   about my business and where each answer is found.
3. Answer what you already know about my business, from memory or from
   files here. Tell me where each answer came from so I can correct it.
   Ask me the rest in one message. Never guess: the legal form and the VAT
   status decide what I file.
4. Run `quints init <directory>` with every answer as a flag. No `--yes`
   and no `--samples`: these are real books.
5. In the new directory, run `uv sync` and `uv run quints check`. Then read
   `AGENTS.md` there and tell me what I should do first.
```

Or do it by hand: [Getting started](https://sealambda.github.io/quints/getting-started/).

`quints init` writes a plain beancount project and commits it to a new git
repository: the Swiss KMU chart of accounts for your legal form,
`quints.toml` for everything entity-specific, an `AGENTS.md` playbook and
skills for a coding agent, and a CI workflow that runs `quints check` on
every pull request. In Claude Code, the skills also install as a plugin:

```text
/plugin install quints --marketplace sealambda/quints
```

## What it does

These run as shown in the sample books.

```bash
# VAT (MWST), effective or Saldosteuersatz method
quints vat liability            # must you register? turnover vs CHF 100'000
quints vat report -p 2026-Q3    # the return, Ziffer by Ziffer
quints vat export -p 2026-Q3    # the return as eCH-0217 XML, for the ESTV portal
quints vat settle -p 2026-Q3    # the settlement entry to paste once filed
quints vat status               # VAT filed but not yet paid, with due dates

# Open items
quints receivables                            # open invoices, aged by due date
quints receivables --remind --at 2026-10-15   # a reminder PDF per overdue invoice
quints payables                               # open supplier bills, aged by due date

# Year-end
quints close check --year 2026                   # what is still open
quints fx revalue --at 2026-12-31                # FX revaluation entry (Art. 960 ff. OR)
quints report bilanz --at 2026-12-31             # balance sheet (OR Art. 959a)
quints report erfolg --year 2026                 # income statement (OR Art. 959b)
quints report statements --year 2026 --lang de   # both, as one PDF

# The gate: ledger, statements, every VAT return not settled yet
quints check --output-format github   # also gitlab, azure, sarif, json
quints check --review                 # plus what is still to do: drafts, missing documents
quints explain vat-mismatch           # what a code means and how to fix it, offline

# Invoicing
quints invoice invoicing/invoices/acme-2026-07.yaml   # QR-bill PDF, cross-checked against the ledger
```

<!-- no-test: needs a statement file, and the network -->
```bash
quints import ubs <statement.mt940>   # drafts into staging/, never into your books
quints prices sync                    # official BAZG/EZV daily CHF rates
```

- **VAT.** Registration, a switch of method, annual filing and
  deregistration are dated entries in `quints.toml`. Each period is computed
  under the rules in force for it. Rates are law, not configuration: they
  ship date-ranged in code.
  [Register, switch method, deregister](https://sealambda.github.io/quints/guides/vat-registration/),
  [File your VAT return](https://sealambda.github.io/quints/guides/vat/).
- **Check.** Every problem prints at its file and line, in the CI runner's
  own format. There is a GitHub action (`sealambda/quints/actions/check`), a
  reusable statements workflow, and `quints diff` and `quints digest` for
  pull-request and weekly summaries:
  [Check the books in CI](https://sealambda.github.io/quints/guides/ci/).
  An agent gets the check after each edit, cut down to what that edit
  brought, and an editor gets it on save (`quints lsp`):
  [Check the books as you edit](https://sealambda.github.io/quints/guides/editing/).
- **Imports.** UBS (MT940), Yapeal (CSV), Wise and Stripe (`--fetch` pulls
  from their APIs). Each draft is flagged for its VAT decision and its
  document. Nothing reaches your books until you review it.
  [Import statements](https://sealambda.github.io/quints/guides/importing/).
- **Invoices.** Domestic or export, as PDF/A with Factur-X data embedded, in
  German, French, Italian, English or Spanish, filed under `documents/`.
  Discounts, pass-through items, credit notes, pro forma and recurring
  invoices: [Invoicing](https://sealambda.github.io/quints/guides/invoicing/).

![Generated Swiss QR-bill invoice PDF with payment
part](docs/assets/invoice-qr-bill.png)

![Generated Bilanz page of the statements PDF, grouped by KMU
code](docs/assets/statements.png)

## Packages

Six distributions live in this repo, each usable on its own in any beancount
setup. Installing `quints` pulls in the other five.

| Package | What it does |
|---|---|
| [`quints`](packages/quints) | the CLI: VAT, invoicing, statements, importing into staging, a Fava extension |
| [`beangulp-mt940`](packages/beangulp-mt940) | beangulp importer for SWIFT MT940 bank statements (UBS et al.) |
| [`beangulp-yapeal`](packages/beangulp-yapeal) | beangulp importer for Yapeal CSV bank statements |
| [`beangulp-wise`](packages/beangulp-wise) | beangulp importer for Wise balance statements, with an SCA-capable API client |
| [`beangulp-stripe`](packages/beangulp-stripe) | beangulp importer for Stripe balance transactions, with a thin API client |
| [`beanprice-bazg`](packages/beanprice-bazg) | beanprice source for official Swiss BAZG/EZV daily FX rates |

## Development

```bash
git clone https://github.com/sealambda/quints
cd quints
uv sync
make check     # ruff, basedpyright, import-linter, deptry, vulture, pytest
```

Architecture, conventions and the other CI checks are in
[CONTRIBUTING.md](CONTRIBUTING.md). Pushing a `vX.Y.Z` tag publishes every
distribution to PyPI.

## License

[GPL-2.0-only](LICENSE), the license beancount itself uses. quints is not
professional tax or legal advice and comes without warranty (GPL-2.0,
sections 11–12). You, or your Treuhänder, stay responsible for what you file
and sign.
