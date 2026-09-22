# Quarterly VAT (MWST)

Everything VAT lives under one command group — `quints vat` — mirroring the
lifecycle: **report** a period, **settle** it, watch what's owed with
**status**, and **convert** foreign amounts along the way.

## The report

```bash
quints vat report -q 2026-Q3
```

Prints the whole of ESTV Form 310 under the effective method, Ziffer by
Ziffer: turnover and its deductions (200–299), the rate rows of section II
(302/303, 312/313, 342/343, 382/383), input VAT (400–479), and what you owe
(500) or are owed (510). Copy the numbers into the ESTV portal; nothing is
filed automatically.

Arbitrary periods work too: `--from 2026-01-01 --to 2026-06-30`.

![The full MWST report on the sample quarter — Form-310 Ziffern plus the Vorsteuer, Bezugsteuer, and revenue detail tables](../assets/mwst.gif)

## The rates

Rates are law (Art. 25 MWSTG), so they ship date-ranged in code — you never
put one in `quints.toml`. The form carries two vintages side by side, one
Ziffer each:

| Rate class | From 01.01.2024 | Ziffer | Until 31.12.2023 | Ziffer |
| --- | --- | --- | --- | --- |
| Normalsatz | 8.1 % | 303 | 7.7 % | 302 |
| Reduzierter Satz (Art. 25 Abs. 2) | 2.6 % | 313 | 2.5 % | 312 |
| Beherbergung (Art. 25 Abs. 4) | 3.8 % | 343 | 3.7 % | 342 |
| Bezugsteuer (Art. 45 ff.) | — | 383 | — | 382 |

quints picks the vintage from the **transaction date**. A supply made in
2023 but invoiced later still belongs in the old column — tag that
transaction `mwst: "old_rate"` and it files under 302/312/342.

Only the rows in force in the period are printed, plus any old-rate row that
actually carries something.

## Which Ziffer does a booking land in?

Four rules, applied in order. The first one that matches wins.

**1. A `mwst:` metadata tag** on the posting, else on the transaction. The
value is a space-separated list of tokens:

| Token | Effect |
| --- | --- |
| `taxable` | domestic taxable turnover (the default) |
| `standard` / `reduced` / `lodging` | the Art. 25 rate class |
| `old_rate` | this supply predates 01.01.2024 → the old Ziffer and rate |
| `optioned` | taxable, and memoed in Ziffer 205 (Art. 22 option) |
| `export_goods` | Ziffer 220 — von der Steuer befreit (Exporte, Art. 23) |
| `export` | Ziffer 221 — Ort der Leistung im Ausland |
| `meldeverfahren` | Ziffer 225 — Übertragung im Meldeverfahren (Art. 38) |
| `exempt` | Ziffer 230 — ausgenommen (Art. 21), ohne Option |
| `reduction` | Ziffer 235 — Entgeltsminderung |
| `diverses` | Ziffer 280 |
| `subvention` | Ziffer 900 — Nicht-Entgelt, outside Ziffer 200 |
| `donation` | Ziffer 910 — Spenden, Dividenden, Schadenersatz |
| `material` / `investment` | input VAT → Ziffer 400 / 405 |
| `einlageentsteuerung` | Ziffer 410 (Art. 32) |
| `vorsteuerkorrektur` | Ziffer 415 (Art. 30/31) |
| `vorsteuerkuerzung` | Ziffer 420 (Art. 33 Abs. 2) |

An unknown token is never ignored — it is listed in the report's
*Prüfung* table, so a typo can't quietly change a return.

```beancount
2026-09-10 * "Altkunde" "2023 supply, invoiced now"
  mwst: "old_rate"
  Assets:CH:GmbH:Receivable:Trade                1077.00 CHF
  Income:CH:GmbH:Consulting:External:Domestic   -1000.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT               -77.00 CHF
```

**2. The account's `kmu:` code**, from the Swiss KMU Kontenrahmen:

- **3800–3899** (Erlösminderungen, Verluste Forderungen) → Ziffer 235. A
  Skonto, a rebate or a Debitorenverlust booked there is deducted from the
  turnover and its OutputVAT reversal reduces the matching rate row.
- an income account **outside 3000–3899** — realised FX gains (6950),
  rounding, Bestandesänderungen (39xx) — is not *Entgelt* and stays out of
  the return entirely.
- for input VAT, the **counter-leg's** code decides between 400 and 405
  (below).

**3. The income-account markers** in `quints.toml`. A marker is matched as a
substring of the account name, most specific first:

```toml
export_marker = ":Export"             # Ziffer 221
export_goods_marker = ":ExportGoods"  # Ziffer 220
exempt_marker = ":Exempt"             # Ziffer 230
optioned_marker = ":Optioned"         # Ziffer 205
reduced_marker = ":Reduced"           # 2.6 % → Ziffer 313
lodging_marker = ":Lodging"           # 3.8 % → Ziffer 343
```

So `Income:CH:GmbH:Books:Reduced` is reduced-rate turnover without any
metadata, and `Income:CH:GmbH:Trade:ExportGoods` lands in 220 rather than
221. Set a marker to `""` to disable it.

**4. Otherwise**: domestic turnover at the standard rate.

Ziffer 200 is the gross worldwide turnover; 289 adds up 220–280; 299 is
200 − 289 and always equals the section-II rate rows' net. If it doesn't,
some turnover landed nowhere and the report says so.

## Input VAT: 400 vs 405

The form splits the deduction in two, and quints decides from the **KMU code
of the dominant counter-leg** — the posting with the largest CHF weight:

- Kontenklasse **4** (Material-, Waren-, Dienstleistungsaufwand) → **400**
- everything else — investments **1400–1799**, personnel and übriger
  Betriebsaufwand **5/6/7**, or no code at all → **405**

For a consulting company that means almost everything is 405; the total
(479) is the same either way, but the form asks for the split.

Tag the InputVAT posting `mwst: "material"` or `mwst: "investment"` to
override it, and `einlageentsteuerung` / `vorsteuerkorrektur` /
`vorsteuerkuerzung` for the 410/415/420 lines:

```beancount
2026-07-03 * "Eigenverbrauch" "Vorsteuerkorrektur (Art. 31)"
  Assets:CH:GmbH:Tax:InputVAT   -30.00 CHF
    mwst: "vorsteuerkorrektur"
  Expenses:CH:GmbH:Material      30.00 CHF
```

479 = 400 + 405 + 410 − 415 − 420, exactly as the form computes it.

## What you owe — or what you're owed

`399 − 479` is the quarter's result. Positive, it prints as **Ziffer 500**
(zu bezahlender Betrag); negative, as **Ziffer 510** (Guthaben der
steuerpflichtigen Person) — a claim on the ESTV that the settlement books as
a debit balance on `PayableVAT` and `quints vat status` reports as a
negative amount until it is refunded or offset.

In `--json`, `z500` stays the *signed* net (the figure the settlement
posts); `z510` carries the credit as a positive number.

## The consistency check

Every transaction with turnover is verified: the output VAT it posts must
equal net × rate, within a few Rappen. A mismatch — the old rate applied by
habit, a missing VAT leg, VAT on an exempt supply — is listed in the report
and in `--json` under `violations`, rather than silently mis-filed.

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
quints vat convert 7.53 EUR 2026-08-01                     # CHF InputVAT posting to paste
quints vat convert 100 EUR 2026-08-01 --bezugsteuer        # reverse charge: InputVAT + Bezugsteuer pair
quints vat convert 100 EUR 2026-08-01 --net --rate reduced # 2.6 % instead of 8.1 %
```

`--bezugsteuer` (Art. 45 MWSTG) is for services bought from abroad — a
foreign SaaS invoice, typically. The pair nets to zero when you can fully
deduct input VAT, but it must be declared (Ziffern 383 and 400/405).
`--rate` works there too, and the emitted block carries the matching
`mwst:` tag so the report values it at the same rate it was computed with.

## For agents

```bash
quints vat report -q 2026-Q3 --json
```

Every Ziffer is a `zNNN` key with a decimal string; `rate_rows` carries the
section-II lines with their rate class and vintage, and `violations` lists
anything the form can't represent. Keys are only ever added.

!!! note "Rates are law, not configuration"
    VAT rates ship date-ranged in code. If a rate changes, you update quints —
    you never edit a rate in your project. The effective method is supported;
    `quints init` rejects saldo rather than mapping it wrong.

    The Ziffern, labels and arithmetic follow ESTV form
    [MWST-4470, *Abrechnung nach der effektiven Methode*, gültig ab
    01.01.2024](https://www.estv2.admin.ch/mwst/formulare/mwst-form-abr-muster-2024-4470-eff-de.pdf).
