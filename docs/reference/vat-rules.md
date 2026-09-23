# How quints fills the VAT return

What `quints vat report` does with each booking: which Ziffer it lands in, at
which rate, and what it checks. Read this to predict a result or tag a booking
that the defaults would put somewhere else. The steps for filing are in
[File your VAT return](../guides/vat.md).

## The rates

Rates are law,[^rates] so they ship with quints, dated. You never put one in
`quints.toml`. The form has two rate vintages side by side, one Ziffer each:

| Rate class | From 01.01.2024 | Ziffer | Until 31.12.2023 | Ziffer |
| --- | --- | --- | --- | --- |
| Normalsatz | 8.1 % | 303 | 7.7 % | 302 |
| Reduzierter Satz (Art. 25 Abs. 2) | 2.6 % | 313 | 2.5 % | 312 |
| Beherbergung (Art. 25 Abs. 4) | 3.8 % | 343 | 3.7 % | 342 |
| Bezugsteuer (Art. 45 ff.) | — | 383 | — | 382 |

quints picks the vintage from the **transaction date**. A supply made in
2023 but invoiced later still belongs in the old column: tag that transaction
`mwst: "old_rate"` and it files under 302/312/342. The report prints only
the rows in force in the period, plus any old-rate row that carries an
amount.

The Saldosteuersätze the ESTV can grant are law too,[^sss-rates] so a rate in
`quints.toml` that isn't on the list is rejected:

| From 01.01.2024 | 0.1 | 0.6 | 1.3 | 2.1 | 3.0 | 3.7 | 4.5 | 5.3 | 6.2 | 6.8 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **Until 31.12.2023** | 0.1 | 0.6 | 1.2 | 2.0 | 2.8 | 3.5 | 4.3 | 5.1 | 5.9 | 6.5 |

Since 2025 a business can hold several rates: the ESTV grants one for every
activity worth more than 10 % of taxable turnover.

## Which Ziffer a booking lands in

Four rules, applied in order. The first one that matches wins.

**1. A `mwst:` tag** on the posting, or else on the transaction. The value is
a space-separated list of tokens:

| Token | Effect |
| --- | --- |
| `taxable` | domestic taxable turnover (the default) |
| `standard` / `reduced` / `lodging` | the Art. 25 rate class |
| `old_rate` | a supply made before 01.01.2024 → the old Ziffer and rate |
| `optioned` | taxable, and also counted in memo Ziffer 205 (Art. 22 option) |
| `export_goods` | Ziffer 220 — von der Steuer befreit (Exporte, Art. 23) |
| `export` | Ziffer 221 — Ort der Leistung im Ausland |
| `meldeverfahren` | Ziffer 225 — Übertragung im Meldeverfahren (Art. 38) |
| `exempt` | Ziffer 230 — ausgenommen (Art. 21), ohne Option |
| `reduction` | Ziffer 235 — Entgeltsminderung |
| `diverses` | Ziffer 280 |
| `subvention` | Ziffer 900 — Nicht-Entgelt, outside Ziffer 200 |
| `donation` | Ziffer 910 — Spenden, Dividenden, Schadenersatz |
| `material` / `investment` | input VAT → Ziffer 400 / 405 |
| `einlageentsteuerung` | Ziffer 410 (Art. 32; the switch to the effective method) |
| `vorsteuerkorrektur` | Ziffer 415 (Art. 30/31; the switch to the Saldosteuersatz) |
| `vorsteuerkuerzung` | Ziffer 420 (Art. 33 Abs. 2) |
| `sss=6.2` | Saldosteuersatz only: the granted rate this supply falls under |

An unknown token is never ignored. It is listed in the report's *Prüfung*
table, so a typo can't quietly change a return.

```beancount
2026-09-10 * "Altkunde" "2023 supply, invoiced now"
  mwst: "old_rate"
  Assets:CH:GmbH:Receivable:Trade                1077.00 CHF
  Income:CH:GmbH:Consulting:External:Domestic   -1000.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT               -77.00 CHF
```

**2. The account's `kmu:` code**, from the Swiss KMU Kontenrahmen:

- **3800–3899** (Erlösminderungen, Verluste Forderungen) → Ziffer 235. A
  Skonto, a rebate or a Debitorenverlust booked there is deducted from
  turnover, and its OutputVAT reversal reduces the matching rate row.
- **Income outside 3000–3899** isn't *Entgelt* and stays out of the return:
  realised FX gains (6950), rounding, Bestandesänderungen (39xx).
- **Input VAT** is split into 400 or 405 by the **counter-leg's** code
  (below).

**3. The income-account markers** in `quints.toml`. Each is matched as a
substring of the account name, most specific first:

```toml
export_marker = ":Export"             # Ziffer 221
export_goods_marker = ":ExportGoods"  # Ziffer 220
exempt_marker = ":Exempt"             # Ziffer 230
optioned_marker = ":Optioned"         # Ziffer 205
reduced_marker = ":Reduced"           # 2.6 % → Ziffer 313
lodging_marker = ":Lodging"           # 3.8 % → Ziffer 343
```

So `Income:CH:GmbH:Books:Reduced` is reduced-rate turnover without any tag,
and `Income:CH:GmbH:Trade:ExportGoods` lands in 220 rather than 221. Set a
marker to `""` to turn it off.

**4. Otherwise**, a booking is domestic turnover at the standard rate.

Ziffer 200 is the gross worldwide turnover; 289 adds up 220–280; 299 is
200 − 289 and always equals the net of the section-II rate rows. If it
doesn't, some turnover landed nowhere, and the report says so.

## Input VAT: 400 vs 405

The form splits the deduction in two. quints decides from the **KMU code of
the dominant counter-leg**, the posting with the largest CHF amount:

- Kontenklasse **4** (Material-, Waren-, Dienstleistungsaufwand) → **400**
- everything else → **405**: investments (**1400–1799**), personnel and
  übriger Betriebsaufwand (**5/6/7**), or no code at all

For a consulting company, almost everything is 405. The total (479) is the
same either way, but the form asks for the split. A tag overrides it.

```beancount
2026-07-03 * "Eigenverbrauch" "Vorsteuerkorrektur (Art. 31)"
  Assets:CH:GmbH:Tax:InputVAT   -30.00 CHF
    mwst: "vorsteuerkorrektur"
  Expenses:CH:GmbH:Material      30.00 CHF
```

479 = 400 + 405 + 410 − 415 − 420, exactly as the form computes it.

## What you owe, or are owed

`399 − 479` is the period's result.

- **Positive:** it prints as **Ziffer 500** (zu bezahlender Betrag).
- **Negative:** it prints as **Ziffer 510** (Guthaben der steuerpflichtigen
  Person). That is a claim on the ESTV: the settlement books it as a debit
  balance on `PayableVAT`, and `quints vat status` shows it as a negative
  amount until it is refunded or offset.

## The consistency check

Every transaction with turnover is checked: the output VAT it posts must
equal net × rate, within a few Rappen. The report lists any mismatch rather
than filing it wrongly, and so does `--json` under `violations`. Typical
causes are the old rate applied by habit, a missing VAT leg, or VAT on an
exempt supply.

## Under the Saldosteuersatz

- **Section I** has the same Ziffern 200–299, but every figure **includes
  VAT**.[^sss-gross] The statutory VAT a sale charged is part of its turnover.
- **Section II** is one turnover line per vintage (323; 322 until
  31.12.2023), split across the granted rates, plus Bezugsteuer (383/382) at
  the *statutory* rate. There is no input-VAT block.[^sss-form]
- **Input VAT:** any InputVAT posting is a violation. Purchases are booked
  gross.
- **Several rates:** a supply goes to the rate whose `marker` its income
  account carries, else to the first rate without one. `mwst: "sss=1.3"`
  pins a single booking.
- **Excluded accounts:** the `saldo_difference` and `bezugsteuer_expense`
  accounts are kept out of the return by name, so a hand-written settlement
  can't file them wrongly.

!!! warning "Ziffer 415 means two different things"
    On the effective form, 415 is *Vorsteuerkorrekturen* (mixed use,
    Eigenverbrauch, and the switch to the Saldosteuersatz). On the
    Saldosteuersatz form, 415 is *Korrekturen bei Übernahme im
    Meldeverfahren*.[^sss-form] quints fills neither form's 415 from the
    other's meaning, and doesn't produce the Saldosteuersatz form's 415.

## The periods and their dates

- **Filing period:** quarters for the effective method, half-years for the
  Saldosteuersatz, or a year on request.[^periods]
- **Periods quints accepts:** `-p 2026-Q3`, `2026-H2`, `2026`, or any
  `--from/--to` range. A range must not span a change of method (see
  [Register, switch method, deregister](../guides/vat-registration.md)).
- **Registration:** the period is clamped to it, and the report prints the
  stretch it covered.
- **Due date:** `quints vat settle` writes `due:` 60 days after the period
  ends.[^due]

## JSON

`quints vat report --json` returns:

- **`zNNN`:** each Ziffer as a key, with a decimal string. `z500` is the
  *signed* net (the figure the settlement posts); `z510` carries a credit as
  a positive number.
- **`rate_rows`:** the section-II lines, with their rate class, vintage and,
  under the Saldosteuersatz, the granted rate's `label`.
- **`violations`:** anything the form can't represent.
- **`vat_method`**, **`form`**, **`liable_from`** / **`liable_to`** (the
  stretch actually covered), and **`notices`**.

Keys are only ever added, never renamed or removed.

[^rates]: Art. 25 MWSTG, [SR 641.20](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_25). In quints: `quints.ledger.VAT_RATES`.
[^sss-rates]: Verordnung der ESTV über die Höhe der Saldosteuersätze nach Branchen und Tätigkeiten, [SR 641.202.62](https://www.fedlex.admin.ch/eli/cc/2024/500/de) (5 September 2024, in force since 1 January 2025). In quints: `quints.ledger.SALDO_RATES`.
[^sss-gross]: [MWST-Info 12 *Saldosteuersätze*](https://www.gate.estv.admin.ch/mwst-webpublikationen/public/pages/taxInfos/tableOfContent.xhtml?publicationId=1004992), Ziff. 18.1.
[^sss-form]: The ESTV's sample returns: [effective method](https://www.estv2.admin.ch/mwst/formulare/mwst-form-abr-muster-effektiv-de.pdf), [Saldosteuersatz](https://www.estv2.admin.ch/mwst/formulare/mwst-form-abr-muster-sss-de.pdf). Since 2025, returns are filed online only.
[^periods]: Art. 35 Abs. 1 and 1bis MWSTG, [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_35).
[^due]: Art. 71 Abs. 1 (the return) and Art. 86 Abs. 1 MWSTG (the payment): "Innert 60 Tagen nach Ablauf der Abrechnungsperiode". [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_86).
