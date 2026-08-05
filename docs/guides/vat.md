# Quarterly VAT (MWST)

Everything VAT lives under one command group — `quints vat` — mirroring the
lifecycle: **report** a period, **settle** it, watch what's owed with
**status**, and **convert** foreign amounts along the way.

## The report

```bash
quints vat report -q 2026-Q3
```

Prints the quarter's Form-310 Ziffern — turnover (200/221/299), output VAT
(303), Bezugsteuer (382), input VAT (400), and the amount owed (500) — mapped
exactly to the ESTV return. Copy the numbers into the ESTV portal; nothing is
filed automatically.

Arbitrary periods work too: `--from 2026-01-01 --to 2026-06-30`.

![The full MWST report on the sample quarter — Form-310 Ziffern plus the Vorsteuer, Bezugsteuer, and revenue detail tables](../assets/mwst.gif)

## Close the quarter

```bash
quints vat settle -q 2026-Q3
```

Prints the same report followed by the settlement transaction to paste into
`books/<year>.bean`: it empties OutputVAT/InputVAT/Bezugsteuer into
`PayableVAT` so the quarter is closed and the next report starts clean.

```bash
quints vat status
```

Shows what's been settled but not yet paid to the ESTV, with due dates
(60 days after quarter end).

## Foreign-currency VAT

VAT must be booked in CHF at the official rate of the invoice date. Don't
compute it by hand:

```bash
quints vat convert 7.53 EUR 2026-08-01                  # CHF InputVAT posting to paste
quints vat convert 100 EUR 2026-08-01 --bezugsteuer     # reverse charge: InputVAT + Bezugsteuer pair
```

`--bezugsteuer` (Art. 45 MWSTG) is for services bought from abroad — a
foreign SaaS invoice, typically. The pair nets to zero when you can fully
deduct input VAT, but it must be declared (Ziffern 382 and 400).

!!! note "Rates are law, not configuration"
    VAT rates ship date-ranged in code. If a rate changes, you update quints —
    you never edit a rate in your project. The effective method is supported;
    `quints init` rejects saldo rather than mapping it wrong.
