# quints

Plain-text accounting for Swiss micro-companies, on top of
[beancount](https://github.com/beancount/beancount) and
[Fava](https://github.com/beancount/fava).

This monorepo holds the `quints` CLI and the standalone building blocks it is
made of — each useful on its own in any beancount setup:

| Package | What it does |
|---|---|
| [`quints`](packages/quints) | Swiss VAT (MWST) reports & settlement, Bezugsteuer helpers, QR-bill invoicing, KMU statutory statements (OR Art. 959a/959b), statement importing into a review staging area, Fava extension |
| [`beangulp-mt940`](packages/beangulp-mt940) | beangulp importer for SWIFT MT940 bank statements (UBS et al.) |
| [`beangulp-wise`](packages/beangulp-wise) | beangulp importer for Wise balance statements, with an SCA-capable API client |
| [`beangulp-stripe`](packages/beangulp-stripe) | beangulp importer for Stripe balance transactions, with a thin API client |
| [`beanprice-bazg`](packages/beanprice-bazg) | beanprice source for official Swiss BAZG/EZV daily FX rates |

## Quick start

```bash
uv add quints          # or: pip install quints
uv run quints --help
```

Point it at your ledger and describe your entity in a `quints.toml` next to
`main.bean` — see [`packages/quints/examples/quints.toml`](packages/quints/examples/quints.toml).
Everything entity-specific (name, VAT registration, account names, importer
rules) is configuration; VAT rates are law and ship date-ranged in code.

```bash
uv run quints mwst -q 2026-Q3        # MWST report, ESTV Ziffer mapping
uv run quints status                 # what's owed, what's due
uv run quints import ubs st.mt940    # draft statement activity into staging/
uv run quints report statements --year 2026 --lang de   # Bilanz + Erfolgsrechnung PDF
```

## Development

```bash
uv sync                # installs all workspace packages, editable
uv run pytest packages
```

## Licensing

Everything is GPL-2.0-only, matching beancount/beangulp/beanprice. (AGPL is
not an option here: GPL-2.0-only code cannot be combined with any v3-family
license.) See each package's LICENSE file.
