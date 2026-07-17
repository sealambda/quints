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
vat_method = "effective"            # saldo is not supported
vat_registered_since = 2026-01-01   # earlier periods are pre-liability
operating_currency = "CHF"
```

`vat_registered_since` clamps VAT reports — quarters before it are reported
as pre-liability instead of producing a wrong return.

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
income_prefix = "Income:CH:Einzelfirma"
export_marker = ":Export"           # income sub-account marker → Ziffer 221
income_domestic = "Income:CH:Einzelfirma:Consulting:External:Domestic"
income_export = "Income:CH:Einzelfirma:Consulting:External:Export"
fx_gain = "Income:CH:Einzelfirma:FX:CurrencyGain"
fx_loss = "Expenses:CH:Einzelfirma:FX:CurrencyLoss"
rounding_income = "Income:CH:Einzelfirma:Rounding"
```

Income accounts containing `export_marker` count as supply abroad
(Ziffer 221); everything else under `income_prefix` is domestic turnover.

## `[report]`

```toml
[report]
language = "en"    # or "de"; --lang overrides per command
```

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
which the scaffold gitignores.
