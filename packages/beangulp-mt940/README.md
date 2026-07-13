# beangulp-mt940

[beangulp](https://github.com/beancount/beangulp) importer for SWIFT **MT940**
bank statements (tested against UBS Switzerland exports), built on the
[`mt-940`](https://github.com/WoLpH/mt940) parser.

## What it does

- Drafts one beancount transaction per `:61:` statement line, with the bank's
  unique entry reference (the part after `//`) as `mt940_ref:` metadata.
  `extract()` skips references already present anywhere in the existing
  ledger — **re-running an import is always safe**.
- Emits a `balance` assertion from the `:62F:` closing balance, dated the day
  after (beancount balances are beginning-of-day).
- Skips zero-amount noise entries (e.g. UBS "Balance closing of service
  prices").
- Optional `payee_rules` draft the counter leg: an iterable of
  `(regex, account, flag)` matched case-insensitively against the payee and
  the full `:86:` details. Unmatched entries keep the `!` review flag and the
  cash leg only — classification, VAT treatment, and document evidence stay a
  human decision.

## Usage

```python
from beangulp_mt940 import Importer

importer = Importer(
    "Assets:Bank:Checking",
    iban="CH93 0076 2011 6238 5295 7",   # identify() filter, optional
    payee_rules=(
        (r"client ag", "Assets:Receivable", "*"),
        (r"hosting",   "Expenses:IT:Hosting", "!"),
    ),
)
entries = importer.extract("statement.mt940", existing=ledger_entries)
```

Works standalone (as above) or as a regular importer in a beangulp
`import_config.py`.

## Notes on UBS specifics

- UBS puts the counterparty in `:86:` after a transaction code and `?`
  (`DE1?Google Workspace …`); the importer's payee heuristic handles both
  this and plain details.
- The supplementary line under `:61:` ("Debit card payment", "e-banking
  credit", …) becomes the narration.
- Transaction dates use the **value date**; the entry (booking) date is
  available in the source if you need it.
