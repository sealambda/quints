# Close the year

A fiscal year is closed when the books stop moving: every VAT period settled,
every bank balance tied to a statement, the year's FX difference and
depreciation booked. `quints close check` is that list, computed from your
ledger. The other commands print the entries it asks for. You review and
paste them: quints never writes your books.

!!! abstract "Applies if"
    - **Legal form:** Einzelfirma, GmbH or AG. The checklist is the same for
      all three.[^books]
    - **VAT status:** any.
        - *Not registered:* the VAT item passes.
        - *Registered:* it expects one settlement per filing period of the
          year (quarters, half-years or the whole year, whichever applied
          that year), from the registration date to its end.
    - **Fiscal year:** the calendar year, like the books (one file per year).
      A GmbH's or AG's first year may run longer: see
      [An extended first year](#an-extended-first-year).

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
6. **Archive the closed year** ([below](#keep-the-books-for-ten-years)).

A registered business also runs `quints vat liability --at 2026-12-31`. If
turnover stayed below the threshold, it says whether you may deregister and
by when ([Register, switch method, deregister](vat-registration.md)).

## An extended first year

A GmbH or AG may skip the closing in the calendar year it was founded. Its
first fiscal year then ends by 31 December of the next year: founded
4 July 2025, first closing 31 December 2026.[^fy] Zürich wants the extension
reported to the Kantonales Steueramt; check what your canton asks.[^zh] An
Einzelfirma can't extend: it closes every calendar year.[^fy-ef]

```bash
quints close check --year 2025
quints close check --year 2026
quints close depreciation --year 2026
quints report statements --year 2026 --from 2025-07-04
```

1. **Check both calendar years.** `close check` works per calendar year, so
   the founding year's VAT periods, flagged entries and missing documents
   only show under `--year 2025`. Ignore its year-end items there (`fx`,
   `prices`, `depreciation`, `receivables`): nothing closes on
   31 December 2025.
2. **Depreciate once, for 2026.** Don't run `close depreciation --year 2025`.
   The 2026 run charges one year's depreciation, 2025 purchases included.
   Whether the longer first year allows more is for you and your Treuhänder
   to decide.
3. **Report from the founding date.** With `--from`, both statements cover
   4 July 2025 to 31 December 2026, and none of it is Gewinnvortrag. `report
   bilanz` and `report erfolg` take the same dates:

    ```bash
    quints report bilanz --at 2026-12-31 --from 2025-07-04
    quints report erfolg --from 2025-07-04 --to 2026-12-31
    ```

4. **From 2027 on, drop `--from`.** The fiscal year is the calendar year
   again, and the first year's result is Gewinnvortrag.

## The checklist

```bash
quints close check --year 2026
```

Every item is **pass**, **warn** or **fail**, with the counts, accounts and
dates behind it and the command that fixes it.

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

A **fail** must be booked or fixed before the numbers are final. A **warn**
is your call: an old receivable may deserve a Delkredere or a write-off, a
missing document may not exist.

- **Balance assertions are read at the start of their date.** An assertion
  dated `2026-12-31` verifies the balance *before* that day's bookings, so
  only `2027-01-01 balance …` ties the year end to your December statement.
  The check looks for that date, and the generated entries below use it.
- **Generated entries are exempt from `documents`:** a VAT settlement or its
  payment (the `^VAT-…` link), the FX revaluation (its only income/expense
  legs are the configured gain/loss, rounding and depreciation accounts) and
  the depreciation entry (its `depreciation_year:` marker). No supplier PDF
  stands behind them.
- **The command exits 0**, because a year mid-close is the normal case.
  `--strict` exits 1 while an item fails: gate a CI job or an agent loop on
  it. `--json` returns the same items with `ok`, `failed` and `warned`
  counters. `--output-format` (`github`, `gitlab`, `azure`, `sarif`)
  annotates each ledger problem at its file and line.

## Depreciation

```bash
quints close depreciation --year 2026
```

Prints the year's depreciation entry[^960a] to paste into
`books/2026.bean`, with a balance assertion per asset. The entry carries
`depreciation_year: "2026"`, so a second run after booking finds the charge
and prints nothing to paste.

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
plus the year's purchases. Linear applies it to historical cost. Both stop at
`residual:`. An asset bought during the year gets a full year of
depreciation, the Swiss small-business default. `prorata = "months"` in
`quints.toml` counts from the month of acquisition instead.

### Maximum rates: ESTV Merkblatt A/1995

`depreciation_category:` is checked against the *Normalsätze* of the ESTV's
Merkblatt A/1995.[^a1995] The rates are percentages **of book value**. The
Merkblatt's footnote 3 halves them when you depreciate from the
Anschaffungswert, which is what `linear` does.

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
supplies, …) is in `quints.closing.MERKBLATT_A1995`. A rate above the maximum
is a **warning**, not a refusal: the books are legal, but the excess needs a
reason your Steuerverwaltung accepts.

### Direct or indirect

By default the charge writes the asset down (**direct**). To book
**indirectly**, set `depreciation_contra:` on the asset to a
Wertberichtigung account from the KMU Kontenrahmen (1509, 1519, 1529, 1539,
…). Cost stays on the asset, the accumulated depreciation sits next to it,
and both roll up to the same Bilanz row (Sachanlagen, KMU 1500–1699).
`method = "indirect"` in `quints.toml` makes it the default, and quints then
warns about any asset without a contra account.

## Keep the books for ten years

Keep the ledger, the documents it links and the statements for ten years
after the end of the fiscal year.[^keep] VAT records stay until the tax
claim expires, ten years after the period ends, and records about real
estate longer.[^keep-vat] All of it must stay complete, unaltered and
readable.[^gebuev]

Git shows every change, but anyone with access can rewrite its history. Once
the year is closed, fix its state:

```bash
git add -A && git commit -m "Close fiscal year 2026"
git tag -s fy-2026 -m "Fiscal year 2026, closed"
git archive --format=tar.gz -o books-2026.tar.gz fy-2026
shasum -a 256 books-2026.tar.gz > books-2026.tar.gz.sha256
```

1. **Commit everything:** books, `documents/`, the statements PDF.
2. **Sign a tag** for the closed year. A signed tag is verifiable, and moving
   it later shows. Its date is only what the signer's clock said.
3. **Archive the tagged state** and its checksum to storage that records
   when it was stored: a bucket with write-once retention, or a timestamp
   from a trusted timestamping service on the checksum. On modifiable media,
   integrity isn't enough: the time of storage must be provable too.[^gebuev]
4. **Write down how the books are kept:** who books, who reviews, where the
   archive lives. The scaffolded `AGENTS.md` describes the booking procedure
   and is a starting point.[^procedure]

Invoices (PDF/A-3a) and statements (PDF/A-2a) are in the archival PDF
format, so they render the same in ten years.

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
- **A fiscal year that isn't the calendar year**, other than an extended
  first one. Even then, `close check` and `close depreciation` work per
  calendar year ([An extended first year](#an-extended-first-year)).

[^books]: Bookkeeping and financial-reporting duty: Art. 957 ff. OR, [SR 220](https://www.fedlex.admin.ch/eli/cc/27/317_321_377/de#art_957). An Einzelunternehmen below CHF 500'000 turnover may keep simplified accounts (Art. 957 Abs. 2); quints keeps full double-entry books either way.
[^960a]: Art. 960a OR, [fedlex](https://www.fedlex.admin.ch/eli/cc/27/317_321_377/de#art_960_a).
[^a1995]: [Merkblatt A/1995 — Abschreibungen auf dem Anlagevermögen geschäftlicher Betriebe](https://www.estv.admin.ch/dam/de/sd-web/Qyxr5xBfdWDp/dbst-mb-a-1995-geschbetriebe-de.pdf); legal basis Art. 27 Abs. 2 Bst. a, 28 and 62 DBG ([SR 642.11](https://www.fedlex.admin.ch/eli/cc/1991/1184_1184_1184/de)). The table lives in `quints.closing.MERKBLATT_A1995`.
[^fy]: Art. 79 Abs. 3 DBG, [SR 642.11](https://www.fedlex.admin.ch/eli/cc/1991/1184_1184_1184/de#art_79): "In jedem Kalenderjahr, ausgenommen im Gründungsjahr, muss ein Geschäftsabschluss mit Bilanz und Erfolgsrechnung erstellt werden." The year after the founding needs a closing, so the first fiscal year ends by 31 December of that year.
[^fy-ef]: Art. 41 Abs. 3 DBG, [SR 642.11](https://www.fedlex.admin.ch/eli/cc/1991/1184_1184_1184/de#art_41): a closing "in jeder Steuerperiode"; for a natural person the tax period is the calendar year (Art. 40 Abs. 1).
[^zh]: Kantonales Steueramt Zürich, [Steuererklärung juristische Personen](https://www.zh.ch/de/steuern-finanzen/steuern/steuern-juristische-personen/steuererklaerung-juristische-personen.html), section *Steuererklärung im Gründungsjahr*: an extended fiscal year "bis maximal 31. Dezember des Folgejahres", to be reported to the Kantonales Steueramt.
[^keep]: Art. 958f Abs. 1 OR: "Die Geschäftsbücher und die Buchungsbelege sowie der Geschäftsbericht und der Revisionsbericht sind während zehn Jahren aufzubewahren. Die Aufbewahrungsfrist beginnt mit dem Ablauf des Geschäftsjahres." [fedlex](https://www.fedlex.admin.ch/eli/cc/27/317_321_377/de#art_958_f).
[^keep-vat]: Art. 70 Abs. 2 MWSTG (until the absolute limitation period of the tax claim, Art. 42 Abs. 6: ten years after the end of the tax period) and Art. 70 Abs. 3 MWSTG (the longer period for records on real estate, for Einlageentsteuerung and Eigenverbrauch). [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_70).
[^gebuev]: Geschäftsbücherverordnung (GeBüV), [SR 221.431](https://www.fedlex.admin.ch/eli/cc/2002/247/de): Art. 3 (integrity: records can't be changed without the change showing), Art. 9 Abs. 1 Bst. b (modifiable media are allowed when a technical procedure such as a digital signature protects integrity, *and* the time of storage is provable beyond falsification, such as by a timestamp, and the procedure is documented), Art. 10 (records stay readable: migrate them when formats or media age).
[^procedure]: Art. 4 GeBüV: organisation, responsibilities, processes and the infrastructure of the bookkeeping are documented so the records can be understood. [fedlex](https://www.fedlex.admin.ch/eli/cc/2002/247/de#art_4).
