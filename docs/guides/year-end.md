# Close the year

A fiscal year is closed when the books stop moving: every VAT period settled,
every bank balance tied to a statement, the year's FX difference and
depreciation booked. `quints close check` is that list, computed from your
ledger. The rest of the commands here print the entries it asks for — for you
to review and paste, never written for you.

!!! abstract "Applies if"
    - **Legal form:** Einzelfirma, GmbH or AG. The checklist is the same for
      all three.[^books]
    - **VAT status:** any.
        - *Not registered:* the VAT item passes.
        - *Registered:* it expects one settlement per filing period of the
          year (quarters, half-years or the whole year, whichever applied
          that year), from the registration date to its end.
    - **Fiscal year:** the calendar year, like the books (one file per year).

## The order of operations

```bash
quints close check --year 2026
quints fx revalue --at 2026-12-31
quints close depreciation --year 2026
quints report statements --year 2026
```

The steps:

1. **Refresh the rates.** `quints prices sync` needs the network, so it
   isn't in the block above ([FX rates](fx.md)).
2. **Check.** `quints close check` lists what is still open.
3. **Book what the check asks for.** The other commands print the entries.
4. **Check again**, until nothing fails.
5. **Hand the PDF to your Treuhänder** ([Statutory reports](reports.md)).

A registered business also runs `quints vat liability --at 2026-12-31` at
the year end. If turnover stayed below the threshold, it says whether you may
deregister and by when ([Register, switch method, deregister](vat-registration.md)).

## The checklist

```bash
quints close check --year 2026
```

Every item comes back **pass**, **warn** or **fail**, with the specifics —
counts, account names, dates — and the command that fixes it.

| Item | Passes when |
|---|---|
| `ledger` | the ledger loads and every account of the entity (`:CH:GmbH:`, `:CH:Einzelfirma:`, …) carries a valid `kmu:` code |
| `vat` | every VAT period of the year is settled *and* paid (filed but unpaid: warn); not registered: pass |
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

Prints the year's depreciation entry[^960a] to paste into
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
Merkblatt A/1995.[^a1995] The rates are
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

## What quints doesn't do here

- **Book, or file.** Every entry is printed for you to review and paste.
- **Close the P&L into equity**, or appropriate the profit (the dividend
  decision of a GmbH/AG, the owner's share of an Einzelfirma).
- **Judgements:** tax provisions, Delkredere percentages, and whether to
  depreciate less than the maximum. Those are yours, or your Treuhänder's.
  quints computes what follows from the books and the published rates, and
  says so.
- **A fiscal year that isn't the calendar year.**

[^books]: Bookkeeping and financial-reporting duty: Art. 957 ff. OR, [SR 220](https://www.fedlex.admin.ch/eli/cc/27/317_321_377/de#art_957). An Einzelunternehmen below CHF 500'000 turnover may keep simplified accounts (Art. 957 Abs. 2); quints keeps full double-entry books either way.
[^960a]: Art. 960a OR, [fedlex](https://www.fedlex.admin.ch/eli/cc/27/317_321_377/de#art_960_a).
[^a1995]: [Merkblatt A/1995 — Abschreibungen auf dem Anlagevermögen geschäftlicher Betriebe](https://www.estv.admin.ch/dam/de/sd-web/Qyxr5xBfdWDp/dbst-mb-a-1995-geschbetriebe-de.pdf); legal basis Art. 27 Abs. 2 Bst. a, 28 and 62 DBG ([SR 642.11](https://www.fedlex.admin.ch/eli/cc/1991/1184_1184_1184/de)). The table lives in `quints.closing.MERKBLATT_A1995`.
