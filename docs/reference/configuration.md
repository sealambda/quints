# quints.toml

Everything entity-specific lives in `quints.toml`, next to `main.bean`.
`quints init` writes it fully populated; this page is the key-by-key
reference. VAT *rates* are deliberately absent — they are law and ship
date-ranged in code.

Resolution: `--config <path>` > `./quints.toml` > built-in defaults.

## `[entity]`

```toml
[entity]
name = "Jane Doe"
legal_form = "einzelfirma"          # gmbh | ag | einzelfirma
vat_method = "effective"            # effective | saldo (Saldosteuersatz)
vat_registered_since = 2026-01-01   # the first day of VAT liability
operating_currency = "CHF"
```

The VAT keys describe the entity's registration:

| Key | Meaning | Default |
|---|---|---|
| `vat_registered` | `false` for a business that is not registered (below the threshold): no return, no VAT on invoices | `true` |
| `vat_registered_since` | the first day of liability. Reports clamp to it; invoices dated before it carry no VAT | none: liable for as far back as the books go |
| `vat_registered_until` | the last day of liability, after deregistering | none: still registered |
| `vat_method` | the method the registration **started** with. A later switch is a `[[vat.change]]` | `effective` |

The steps behind each key, with the law, are in
[Register, switch method, deregister](../guides/vat-registration.md).

## `[ledger]`

```toml
[ledger]
main = "main.bean"
prices = "prices.bean"
```

## `[accounts]`

Account names mirror `accounts.bean` — change them in both files together.

```toml
[accounts]
entity_marker = ":CH:Einzelfirma:"  # scopes KMU statements and the plugin
input_vat = "Assets:CH:Einzelfirma:Tax:InputVAT"
output_vat = "Liabilities:CH:Einzelfirma:Tax:OutputVAT"
bezugsteuer = "Liabilities:CH:Einzelfirma:Tax:Bezugsteuer"
payable_vat = "Liabilities:CH:Einzelfirma:Tax:PayableVAT"
receivable = "Assets:CH:Einzelfirma:Receivable:Trade"
payable = "Liabilities:CH:Einzelfirma:Payable:Trade"
income_prefix = "Income:CH:Einzelfirma"
export_marker = ":Export"             # Ziffer 221 (Ort der Leistung im Ausland)
export_goods_marker = ":ExportGoods"  # Ziffer 220 (Exporte, Art. 23)
exempt_marker = ":Exempt"             # Ziffer 230 (ausgenommen, Art. 21)
optioned_marker = ":Optioned"         # Ziffer 205 (Option nach Art. 22)
reduced_marker = ":Reduced"           # 2.6 % rate class → Ziffer 313
lodging_marker = ":Lodging"           # 3.8 % Beherbergung → Ziffer 343
income_domestic = "Income:CH:Einzelfirma:Consulting:External:Domestic"
income_export = "Income:CH:Einzelfirma:Consulting:External:Export"
fx_gain = "Income:CH:Einzelfirma:FX:CurrencyGain"
fx_loss = "Expenses:CH:Einzelfirma:FX:CurrencyLoss"
rounding_income = "Income:CH:Einzelfirma:Rounding"
```

The markers are matched as substrings of the account name, most specific
first, and route income to a Form-310 Ziffer or an Art. 25 rate class;
everything unmarked under `income_prefix` is domestic turnover at the
standard rate. Set a marker to `""` to disable it. A `mwst:` metadata tag on
a transaction or posting overrides them, and the account's `kmu:` code
supplies the rest (Erlösminderungen → Ziffer 235, the 400/405 split). See
[How quints fills the VAT return](vat-rules.md).

## `[vat]`

The effective method with quarterly filing needs no `[vat]` section.
Anything else goes here: the filing period, the granted Saldosteuersätze,
and every later change.

```toml
[vat]
period = "half-year"    # quarter | half-year | year

# The Saldosteuersätze the ESTV granted you. The first entry without a
# marker is the default; `marker` sends an income sub-account to another
# rate, and `mwst: "sss=1.3"` pins a single booking.
[[vat.saldo]]
rate = 6.2

[[vat.saldo]]
rate = 1.3
marker = ":Handel"
```

`period` drives the period grid that `quints close check` expects. The
effective method files quarterly and the Saldosteuersatz half-yearly; either
may file annually on request.[^periods] A `rate` the ESTV can't grant is
rejected at load: the permitted list is law,[^sss-rates] so it ships in
quints, dated.

### `[[vat.change]]`

A later change of method, filing period or granted rates, dated:

```toml
[[vat.change]]
from = 2027-01-01          # always a 1 January — the start of a tax period
method = "saldo"           # optional: effective | saldo
period = "half-year"       # optional: quarter | half-year | year
saldo = [{ rate = 6.2 }]   # optional: the rates granted from that date
```

The rules:

- **Order:** list entries oldest first.
- **Inheritance:** each entry keeps what it doesn't name from the phase
  before it.
- **A change of method** resets `period` to that method's default and drops
  the previous rates.
- **Validation:** `quints.toml` fails to load when `from` isn't a 1 January,
  when an entry is out of order, when it names no change, or when a switch
  comes before the minimum stay (three years on the effective method, one
  tax period on the Saldosteuersatz).[^switch] The error names the earliest
  legal date.

With `vat_method = "saldo"`, `[accounts]` also carries the two accounts only
that method books to:

```toml
saldo_difference = "Income:CH:Einzelfirma:VAT:SaldoDifference"
bezugsteuer_expense = "Expenses:CH:Einzelfirma:Tax:Bezugsteuer"
```

The first takes the gap between the VAT your invoices collected and the SSS
you owe; the second takes the reverse charge, which the SSS does not pay back.
Both are kept out of the return by name, so a hand-written settlement can't
file them wrongly. See [File your VAT return](../guides/vat.md).

[^periods]: Art. 35 Abs. 1 and 1bis Bst. b MWSTG, Art. 35a MWSTG. [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_35).
[^sss-rates]: [SR 641.202.62](https://www.fedlex.admin.ch/eli/cc/2024/500/de), in force since 1 January 2025.
[^switch]: Art. 37 Abs. 4 MWSTG, Art. 34 Abs. 2 MWSTG. [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_37).

## `[payables]`

```toml
[payables]
default_terms_days = 30    # due date for a bill that carries no `due:`
```

`quints payables` ages open supplier bills against their due date: the `due:`
metadata on the bill transaction, or its date plus these terms.

## `[report]`

```toml
[report]
language = "en"    # or "de"; --lang overrides per command
```

## `[close]`

```toml
[close]
depreciation_account = "Expenses:CH:GmbH:Depreciation"   # KMU 6800
method = "direct"                   # or "indirect": credit a Wertberichtigung (15x9)
prorata = "full"                    # or "months" in the year of acquisition
receivable_review_days = 90         # open longer at year end → Delkredere review
```

Defaults for the year-end close. Per-asset rates and methods live on the
fixed-asset `open` directives, not here — see [Close the year](../guides/year-end.md).

## `[import.*]`

One section per importer. Rules are `[payee regex, counter account, flag]`
triples — `*` books the draft as complete, `!` leaves it flagged for your VAT
decision and a linked document.

```toml
[import.ubs]
account = "Assets:CH:Einzelfirma:Current:UBS:CHF"
iban = "CH9300762011623852957"      # identifies statements (MT940 :25: field)
rules = [
    ['\backme\b', "Assets:CH:Einzelfirma:Receivable:Trade", "*"],
    ["cloudflare", "Expenses:CH:Einzelfirma:IT:Hosting", "!"],
]

[import.yapeal]
account = "Assets:CH:Einzelfirma:Current:Yapeal:CHF"
iban = "CH9300762011623852957"      # optional — matched against file content
rules = []

[import.wise]
fees_account = "Expenses:CH:Einzelfirma:BankFees:Wise"
holder = "Jane Doe"                 # filter multi-profile API tokens
rules = []

[import.wise.accounts]
CHF = "Assets:CH:Einzelfirma:Current:Wise:CHF"
EUR = "Assets:CH:Einzelfirma:Current:Wise:EUR"

[import.stripe]
account_id = "acct_XXXXXXXXXXXX"    # guard: refuse a key for another account
fees_account = "Expenses:CH:Einzelfirma:BankFees:Stripe"
tax_account = "Assets:CH:Einzelfirma:Tax:InputVAT"
rules = []

[import.stripe.accounts]
EUR = "Assets:CH:Einzelfirma:Current:Stripe:EUR"
```

API credentials never go in `quints.toml` — they live in `.env`
(`QUINTS_WISE_API_TOKEN`, `QUINTS_WISE_PRIVATE_KEY`, `QUINTS_STRIPE_API_KEY`),
which the scaffold gitignores. The Stripe key is a restricted one with
*Balance transaction sources: Read* and *Charges: Read*, plus *Invoices: Read*
if you use `quints import stripe --invoices`.

## Debugging

A problem in your own files — a malformed IBAN, an invoice that doesn't
balance — is reported as a single `ERROR:` line on stderr, with exit code 1.
Set `QUINTS_TRACEBACK=1` to get the Python traceback instead, for when the
error looks like a bug in quints rather than in the books.
