---
title: Swiss bookkeeping as plain text
---

# quints

Swiss bookkeeping as plain text: VAT (MWST) returns, QR-bill invoices and
statutory statements for a GmbH, AG or Einzelfirma, on
[beancount](https://github.com/beancount/beancount) and
[Fava](https://github.com/beancount/fava).

quints is a deterministic CLI with machine-readable output, built to be
driven by an AI coding agent (Claude Code, Codex, …). The agent proposes
bookings; quints checks them and computes every figure you file. It never
calls a model. See [Working with AI agents](reference/ai-agents.md).

## Install

<!-- no-test -->
```bash
uv tool install quints        # or: pipx install quints / pip install quints
```

Python 3.11+ (uv installs it if missing). Typst, for the PDFs, is bundled.

## Sixty seconds

```bash
quints init my-books --samples --yes
cd my-books
quints check
quints vat report -p 2026-Q3
```

Sample books with a quarter booked, and a VAT return on screen. For real
books, [Getting started](getting-started.md) opens with
[a prompt to paste into your AI agent](getting-started.md#hand-it-to-your-ai-agent).

![quints init scaffolds sample books, quints check validates them, and quints vat report prints the VAT return](assets/quickstart.gif)

## By the job

| You need to | Run | Guide |
|---|---|---|
| Know whether you must register for VAT | `quints vat liability` | [Register, switch method, deregister](guides/vat-registration.md) |
| Record a registration, method switch or deregistration | `quints.toml` → `[entity]`, `[[vat.change]]` | [Register, switch method, deregister](guides/vat-registration.md) |
| File a VAT return | `quints vat report -p 2026-Q3`, then `quints vat export` | [File your VAT return](guides/vat.md) |
| Book bank/PSP activity | `quints import ubs <statement.mt940>` | [Import statements](guides/importing.md) |
| See what you owe | `quints payables` | [Import statements](guides/importing.md) |
| Send a QR-bill invoice | `quints invoice <invoice.yaml>` | [Invoicing](guides/invoicing.md) |
| Send the next recurring invoice | `quints invoice new --from <last.yaml> --period 2026-08` | [Invoicing](guides/invoicing.md#the-next-invoice-of-a-recurring-one) |
| Cancel or reduce an invoice | a credit note file, then `quints invoice` | [Invoicing](guides/invoicing.md#cancel-or-reduce-an-invoice-credit-note) |
| Take an advance before the invoice is dated | a pro forma, then `quints invoice` | [Get paid before you invoice](guides/pro-forma.md) |
| Pick QRR or SCOR for your QR-bills | `issuer.yaml` → `bank.<CCY>` | [Payment references](guides/payment-references.md) |
| Check an IBAN and its BIC | `quints iban` | [Invoicing](guides/invoicing.md) |
| See who owes you | `quints receivables` | [Allocate customer payments](guides/receivables.md) |
| Remind overdue invoices | `quints receivables --remind` | [Allocate customer payments](guides/receivables.md#remind-what-is-overdue) |
| Year-end statements | `quints report statements --year 2026` | [Statutory reports](guides/reports.md) |
| Close the year | `quints close check --year 2026` | [Close the year](guides/year-end.md) |
| Keep FX rates right | `quints prices sync` | [FX rates](guides/fx.md) |
| Check every change in CI | `quints check --output-format github` | [Check the books in CI](guides/ci.md) |
| Check every edit, in an editor or an agent | `quints lsp`, `quints check --review` | [Check the books as you edit](guides/editing.md) |
| Understand a problem `quints check` reports | `quints explain <code>` | [Diagnostics](reference/diagnostics.md) |

Every command in these docs runs in the test suite, against the shipped
example project. Every legal rule a page relies on is footnoted to its
source, so you can check it applies to you.

quints is not tax or legal advice, and it comes without warranty
([GPL-2.0](https://github.com/sealambda/quints/blob/main/LICENSE), sections
11–12). You, or your Treuhänder, stay responsible for what you file and sign.

## The building blocks

quints is six PyPI distributions, each usable on its own in any beancount
setup: [`quints`](https://pypi.org/project/quints/),
[`beangulp-mt940`](https://pypi.org/project/beangulp-mt940/),
[`beangulp-yapeal`](https://pypi.org/project/beangulp-yapeal/),
[`beangulp-wise`](https://pypi.org/project/beangulp-wise/),
[`beangulp-stripe`](https://pypi.org/project/beangulp-stripe/) and
[`beanprice-bazg`](https://pypi.org/project/beanprice-bazg/). Installing
`quints` pulls in the other five. Source:
[github.com/sealambda/quints](https://github.com/sealambda/quints).
