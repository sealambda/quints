# quints

Plain-text accounting for Swiss micro-companies — GmbH, AG, or Einzelfirma —
on top of [beancount](https://github.com/beancount/beancount) and
[Fava](https://github.com/beancount/fava).

Swiss VAT (MWST) returns, Bezugsteuer, QR-bill invoicing, statutory KMU
statements, official BAZG FX rates. The things a Swiss micro-company actually
has to do, on a ledger you own as text.

`quints` is deterministic and machine-readable by design, so an AI coding
agent (Claude Code, Codex, …) can drive it: the agent proposes bookings,
`quints` checks and reports. It never calls a model itself. See
[Working with AI agents](reference/ai-agents.md).

## Install

<!-- no-test -->
```bash
uv tool install quints        # or: pipx install quints / pip install quints
quints --help
```

Python 3.10+ (uv installs it if missing). Typst — for the PDFs — comes
bundled. Nothing else to install.

## Sixty seconds

```bash
quints init my-books --samples --yes    # drop --yes to answer the questionnaire
cd my-books
quints check
quints mwst -q 2026-Q3
```

That's a runnable project with a sample quarter booked and a Form-310 VAT
report on screen. [Getting started](getting-started.md) walks through a real
setup — including the legal-form choice.

## By the job

| You need to | Run | Guide |
|---|---|---|
| File quarterly VAT | `quints mwst -q 2026-Q3` | [Quarterly VAT](guides/vat.md) |
| See who owes you | `quints receivables` | [Invoicing](guides/invoicing.md) |
| Send a QR-bill invoice | `quints invoice <invoice.yaml>` | [Invoicing](guides/invoicing.md) |
| Book bank/PSP activity | `quints import ubs <statement.mt940>` | [Import statements](guides/importing.md) |
| Year-end statements | `quints report statements --year 2026` | [Statutory reports](guides/reports.md) |
| Keep FX rates right | `quints prices sync` | [FX rates](guides/fx.md) |

Every command in these docs is executed by the test suite against the shipped
example project. If a documented command breaks, CI fails before you see it.

## The building blocks

`quints` is assembled from five standalone PyPI distributions —
[`quints`](https://pypi.org/project/quints/),
[`beangulp-mt940`](https://pypi.org/project/beangulp-mt940/),
[`beangulp-wise`](https://pypi.org/project/beangulp-wise/),
[`beangulp-stripe`](https://pypi.org/project/beangulp-stripe/),
[`beanprice-bazg`](https://pypi.org/project/beanprice-bazg/) — each usable on
its own in any beancount setup. Installing `quints` pulls them all in. Source:
[github.com/sealambda/quints](https://github.com/sealambda/quints).
