# beangulp-yapeal

[beangulp](https://github.com/beancount/beangulp) importer for
[Yapeal](https://yapeal.ch) CSV bank statements, built on beangulp's
`csvbase`.

## What it does

- Drafts one beancount transaction per CSV row, with Yapeal's
  `Transaktions-ID` as `bank_ref:` metadata (`meta_key=` renames it).
  `extract()` skips references already present anywhere in the existing
  ledger — **re-running an import is safe**.
- Stops with the file and line number on a row without a transaction ID or
  with an unreadable amount. Nothing is skipped silently.
- `identify()` accepts a CSV with Yapeal's header row; pass `iban=` to accept
  only that account's export.
- Optional `payee_rules` draft the counter leg: an iterable of
  `(regex, account, flag)` matched case-insensitively. Unmatched entries keep
  the `!` review flag and the cash leg only — classification, VAT treatment,
  and document evidence stay a human decision.

## Usage

```python
from beangulp_yapeal import Importer

importer = Importer(
    "Assets:Bank:Yapeal",
    iban="CH93 0076 2011 6238 5295 7",  # identify() filter, optional
    payee_rules=(
        (r"client ag", "Assets:Receivable", "*"),
        (r"hosting", "Expenses:IT:Hosting", "!"),
    ),
)
entries = importer.extract("statement.csv", existing=ledger_entries)
```

Works standalone (as above) or as a regular importer in a beangulp
`import_config.py`. The currency defaults to CHF (`currency=`).

## License

GPL-2.0-only.
