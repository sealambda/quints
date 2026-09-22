# Close the year

A fiscal year is closed when the books stop moving: every VAT period settled,
every bank balance tied to a statement, the year's FX difference and
depreciation booked. `quints close check` is that list, computed from your
ledger. The rest of the commands here print the entries it asks for — for you
to review and paste, never written for you.

## The order of operations

```bash
quints close check --year 2026
quints fx revalue --at 2026-12-31
quints close depreciation --year 2026
quints report statements --year 2026
```

Refresh the rates first — `quints prices sync` needs network, so it isn't in
the block above ([FX rates](fx.md)). Then: check, book what the check asks
for, check again, and hand the PDF to your Treuhänder
([Statutory reports](reports.md)).

## The checklist

```bash
quints close check --year 2026
```

Every item comes back **pass**, **warn** or **fail**, with the specifics —
counts, account names, dates — and the command that fixes it.

| Item | Passes when |
|---|---|
| `ledger` | the ledger loads and every `:CH:GmbH:` account carries a valid `kmu:` code |
| `vat` | every VAT quarter of the year is settled *and* paid (filed but unpaid: warn) |
| `flagged` | no `!`-flagged transaction is left in the year |
| `staging` | no importer drafts are waiting in `staging/` |
| `inbox` | `inbox/` holds no unfiled document |
| `documents` | every income/expense transaction links a `document:` |
| `assertions` | every bank account has a balance assertion dated 1 January or later |
| `fx` | the year-end revaluation is booked — no unrealized FX left |
| `prices` | a rate dated 31 December exists for every foreign currency held |
| `depreciation` | the year's write-down is booked on every asset that has a rate |
| `receivables` | nothing has been open longer than `receivable_review_days` |

A **fail** must be booked or fixed before the numbers are final. A **warn** is
a judgement call quints will not make for you: an old receivable may deserve a
Delkredere or a write-off, a missing document may simply not exist.

Two details worth knowing:

- **Balance assertions are read at the start of their date.** An assertion
  dated `2026-12-31` verifies the balance *before* that day's bookings, so
  only `2027-01-01 balance …` ties the year end to your December statement.
  That is the date the check looks for, and the date the generated entries
  below use.
- **Generated entries are exempt from `documents`.** A VAT settlement or its
  payment (recognised by the `^VAT-…` link), the FX revaluation (its only
  income/expense legs are the configured gain/loss, rounding and depreciation
  accounts) and the depreciation entry (its `depreciation_year:` marker) are
  quints' own output — there is no supplier PDF behind them.

The command **reports and exits 0**: a year mid-close is the normal case, and
the example project shipped with quints is exactly that — VAT still to settle,
no year-end assertions yet. Add `--strict` to exit 1 while any item fails,
which is what a CI job or an agent loop should gate on. `--json` returns the
same items with `ok`, `failed` and `warned` counters.

## Depreciation

```bash
quints close depreciation --year 2026
```

Prints the year's depreciation entry (Art. 960a OR) to paste into
`books/2026.bean`, with a balance assertion per asset. Run it again after
booking and it prints nothing: the generated transaction carries
`depreciation_year: "2026"`, which is how quints knows the charge is already
in the books.

### The metadata is the source of truth

Rates and methods live on the fixed-asset `open` directive in `accounts.bean`,
never in a spreadsheet:

```beancount
2026-01-01 open Assets:CH:GmbH:FixedAssets:Equipment CHF
  kmu: "1520"                          ; Büromaschinen, Informatik
  depreciation: "declining"            ; or "linear"
  depreciation_rate: "40"              ; percent
  depreciation_category: "it-equipment"
  residual: "1"                        ; the pro-memoria franc
```

| Key | Meaning |
|---|---|
| `depreciation:` | `declining` (rate on book value) or `linear` (rate on cost) — required |
| `depreciation_rate:` | the rate in percent |
| `useful_life_years:` | linear alternative to a rate: the charge is cost / years |
| `residual:` | book value the asset never falls below — `"1"` is the Swiss pro-memoria franc |
| `depreciation_category:` | an ESTV Merkblatt A/1995 category, for the maximum-rate check |
| `depreciation_account:` | override the expense account for this asset |
| `depreciation_contra:` | book indirectly against this Wertberichtigung account |

Declining balance applies the rate to the book value carried into the year
plus the year's own purchases; linear applies it to historical cost. Both stop
at `residual:`. An asset bought during the year gets a full year of
depreciation — the Swiss small-business default — unless you set
`prorata = "months"` in `quints.toml`, which counts from the month of
acquisition.

### Maximum rates: ESTV Merkblatt A/1995

`depreciation_category:` is checked against the *Normalsätze* of the ESTV's
[Merkblatt A/1995 — Abschreibungen auf dem Anlagevermögen geschäftlicher
Betriebe](https://www.estv.admin.ch/dam/de/sd-web/Qyxr5xBfdWDp/dbst-mb-a-1995-geschbetriebe-de.pdf)
(Rechtsgrundlagen: Art. 27 Abs. 2 Bst. a, 28 und 62 DBG). The rates are
percentages **of book value**; the Merkblatt's footnote 3 halves them when you
depreciate from the Anschaffungswert, which is what `linear` does here.

| Category | Max (declining) | Merkblatt heading |
|---|---|---|
| `buildings-commercial` | 4% | Geschäftshäuser, Büro- und Bankgebäude |
| `buildings-industrial` | 8% | Fabrik-, Lagergebäude, gewerbliche Bauten |
| `furniture` | 25% | Geschäftsmobiliar, Werkstatt- und Lagereinrichtungen |
| `machines` | 30% | Apparate und Maschinen zu Produktionszwecken |
| `vehicles` | 40% | Motorfahrzeuge aller Art |
| `office-machines` | 40% | Büromaschinen |
| `it-equipment` | 40% | Datenverarbeitungsanlagen (Hardware und Software) |
| `intangibles` | 40% | Immaterielle Werte; Goodwill |
| `tools` | 45% | Werkzeuge, Werkgeschirr, Geräte, Paletten |

The full table (buildings with and without land, tanks, rail sidings, hotel
supplies, …) is in `quints.closing.MERKBLATT_A1995`. A rate above the ceiling
is a **warning**, not a refusal: it is legal bookkeeping, but the excess needs
a reason your Steuerverwaltung accepts.

### Direct or indirect

By default the charge writes the asset down (**direct**). Set a
`depreciation_contra:` account on the asset — a Wertberichtigung account from
the KMU Kontenrahmen (1509, 1519, 1529, 1539, …) — to book **indirectly**
instead: cost stays visible on the asset, the accumulated depreciation sits
next to it, and both roll up to the same Bilanz row (Sachanlagen, KMU
1500–1699). `method = "indirect"` in `quints.toml` makes it the default and
makes quints warn about any asset that has no contra account.

## Configuration

```toml
[close]
depreciation_account = "Expenses:CH:GmbH:Depreciation"   # KMU 6800
method = "direct"                   # or "indirect"
prorata = "full"                    # or "months" in the year of acquisition
receivable_review_days = 90         # open longer at year end → Delkredere review
```

## What quints does not do

It does not book anything, does not close the P&L into equity, and does not
file anything. Tax provisions, Delkredere percentages and the decision to
depreciate less than the maximum are yours (or your Treuhänder's) — quints
computes what follows from the books and the published rates, and says so.
