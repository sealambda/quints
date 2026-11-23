# How quints fills the VAT return

What `quints vat report` does with each booking: its Ziffer, its rate, and
what it checks. Read it to predict a result, or to tag a booking the
defaults would put elsewhere. The filing steps are in
[File your VAT return](../guides/vat.md).

## The rates

Rates are law,[^rates] so they ship with quints, dated. You never put one in
`quints.toml`. The form has two rate vintages, one Ziffer each:

| Rate class | From 01.01.2024 | Ziffer | Until 31.12.2023 | Ziffer |
| --- | --- | --- | --- | --- |
| Normalsatz | 8.1 % | 303 | 7.7 % | 302 |
| Reduzierter Satz (Art. 25 Abs. 2) | 2.6 % | 313 | 2.5 % | 312 |
| Beherbergung (Art. 25 Abs. 4) | 3.8 % | 343 | 3.7 % | 342 |
| Bezugsteuer (Art. 45 ff.) | — | 383 | — | 382 |

quints picks the vintage from the **transaction date**. A supply made in
2023 but invoiced later belongs in the old column: tag the transaction
`mwst: "old_rate"` and it files under 302/312/342. The report prints the
rows in force in the period, plus any old-rate row that carries an amount.

The Saldosteuersätze the ESTV can grant are law too:[^sss-rates] a rate in
`quints.toml` that isn't on the list is rejected.

| From 01.01.2024 | 0.1 | 0.6 | 1.3 | 2.1 | 3.0 | 3.7 | 4.5 | 5.3 | 6.2 | 6.8 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **Until 31.12.2023** | 0.1 | 0.6 | 1.2 | 2.0 | 2.8 | 3.5 | 4.3 | 5.1 | 5.9 | 6.5 |

Since 2025 a business can hold several rates: the ESTV grants one for every
activity worth more than 10 % of taxable turnover.

## Which Ziffer a booking lands in

Four rules, in order. The first that matches wins.

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
| `not_entgelt` | outside the form: neither a supply nor a 900/910 flow (a released provision) |
| `material` / `investment` | input VAT → Ziffer 400 / 405 |
| `einlageentsteuerung` | Ziffer 410 (Art. 32; the switch to the effective method) |
| `vorsteuerkorrektur` | Ziffer 415 (Art. 30/31; the switch to the Saldosteuersatz) |
| `vorsteuerkuerzung` | Ziffer 420 (Art. 33 Abs. 2) |
| `sss=6.2` | Saldosteuersatz only: the granted rate this supply falls under |

An unknown token is never ignored. It is listed under *Prüfung*, so a typo
can't quietly change a return.

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
  realised FX gains (6950), rounding, Bestandesänderungen (39xx). The
  exception is a transaction that charges OutputVAT with no 3000–3899 leg,
  such as a side-business sale on 7000. VAT was charged, so it was a
  taxable supply,[^entgelt] and its net is counted with its tax.
- **OutputVAT with no income leg at all**, such as the sale of a fixed asset
  that credits the asset account, declares the Entgelt it implies: the VAT
  divided by the standard rate goes into 200 and 303. The form has no tax
  without turnover. Under the Saldosteuersatz it is listed under *Prüfung*
  instead.
- **Untagged income on 7000–8999** (betrieblicher Nebenerfolg,
  betriebsfremder or ausserordentlicher Ertrag) without OutputVAT is listed
  under *Prüfung* with its amount, and stays out of the figures. Its code
  can't tell whether it is Entgelt: exempt rent belongs in 200 and 230,
  Schadenersatz in 910. Tag it `exempt`, `taxable`, `donation`, `subvention`
  or `not_entgelt`. Financial income (6950–6999) and FX gains are not
  affected.
- **Input VAT** is split into 400 or 405 by the **counter-leg's** code
  (below).

**3. The income-account markers** under `[accounts]` in `quints.toml`.
Each is matched as a substring of the account name, most specific first:

```toml
[accounts]
export_marker = ":Export"             # Ziffer 221
export_goods_marker = ":ExportGoods"  # Ziffer 220
exempt_marker = ":Exempt"             # Ziffer 230
optioned_marker = ":Optioned"         # Ziffer 205
reduced_marker = ":Reduced"           # 2.6 % → Ziffer 313
lodging_marker = ":Lodging"           # 3.8 % → Ziffer 343
```

So `Income:CH:GmbH:Books:Reduced` is reduced-rate turnover without a tag,
and `Income:CH:GmbH:Trade:ExportGoods` lands in 220, not 221. Set a marker
to `""` to turn it off.

**4. Otherwise**, a booking is domestic turnover at the standard rate.

Ziffer 200 is the gross worldwide turnover. 289 adds up 220–280. 299 is
200 − 289 and always equals the net of the section-II rate rows. If it
doesn't, some turnover landed nowhere, and the report says so.

## Input VAT: 400 vs 405

quints splits the deduction by the **KMU code of the dominant counter-leg**,
the posting with the largest CHF amount:

- Kontenklasse **4** (Material-, Waren-, Dienstleistungsaufwand) → **400**
- everything else → **405**: investments (**1400–1799**), personnel and
  übriger Betriebsaufwand (**5/6/7**), or no code at all

For a consulting company, almost everything is 405. A tag overrides the
split:

```beancount
2026-07-03 * "Eigenverbrauch" "Vorsteuerkorrektur (Art. 31)"
  Assets:CH:GmbH:Tax:InputVAT   -30.00 CHF
    mwst: "vorsteuerkorrektur"
  Expenses:CH:GmbH:Material      30.00 CHF
```

479 = 400 + 405 + 410 − 415 − 420, as the form computes it.

**Credits to InputVAT.** A supplier's credit note credits InputVAT together
with the expense or investment it reduces. That credit reduces 400 or 405 in
the period it is booked.[^art41] A credit with no such leg (input VAT moved
into cost) is ambiguous:

- **Dated on `vat_registered_since`,** it is read as the reversal of input
  VAT booked before you were liable. That was never deducted, so it stays
  out of the return.
- **On any other day,** tag it `vorsteuerkorrektur` or `vorsteuerkuerzung`.
  Untagged, it is listed under *Prüfung* and left out.

A Bezugsteuer debit outside a settlement (a foreign supplier's credit note)
reduces 383/382 the same way.

## What you owe, or are owed

`399 − 479` is the period's result.

- **Positive:** **Ziffer 500** (zu bezahlender Betrag).
- **Negative:** **Ziffer 510** (Guthaben der steuerpflichtigen Person), a
  claim on the ESTV. The settlement books it as a debit balance on
  `PayableVAT`, and `quints vat status` shows it as a negative amount until
  it is refunded or offset.

## The tax is turnover × rate

The return declares turnover. Each row's tax is its turnover × its rate, and
399 is the sum of those products, rounded to the Rappen once at the
end.[^ech-calc] The ESTV portal computes it the same way and rejects an
uploaded return whose 500/510 differs.

So the OutputVAT your invoices posted is checked, not declared. On every
transaction with turnover it must equal net × rate, within a few Rappen. A
mismatch is listed in the report, and in `--json` under `violations`.
Typical causes: the old rate applied by habit, a missing VAT leg, VAT on an
exempt supply. A sale with legs at different rates (100.00 at 8.1 % and
1000.00 at 2.6 %, posting 34.10) is checked as a whole, and each net lands
on its own rate row.

Invoices round their VAT one by one, so a period's OutputVAT can sit a few
Rappen from the tax declared. `quints vat settle` books that difference to
`rounding_income`, and OutputVAT still closes at zero.

Every Ziffer is rounded to the Rappen once. 289, 299 and 479 are sums of
rounded figures. 399 is the sum of the unrounded products, so it can differ
by a Rappen from the tax column added up by hand. A Rappen of rounding left
between 299 and the rate rows goes to the largest row.

## Foreign currency

An amount in another currency is valued at the rate in `prices.bean` for its
date, or the latest one before it. With no rate at all, the amount is left
out of the return and listed, never counted as CHF 0: run
`quints prices sync`.

A VAT leg (OutputVAT, InputVAT, Bezugsteuer) booked in another currency is
counted at its CHF value and listed, because the return and the settlement
are in CHF. Book VAT legs in CHF with `quints vat convert`.

Ledger loader errors are listed first. A file the loader couldn't read, or
a transaction with a syntax error, is missing from every Ziffer.

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
- **Activities:** for periods from 2025 on, the return lists turnover per
  activity.[^ech-activity] `activity` on a `[[vat.saldo]]` entry holds the
  portal's code for it, and `quints vat export` needs one per granted rate.
- **Excluded accounts:** the `saldo_difference` and `bezugsteuer_expense`
  accounts are kept out of the return by name, so a hand-written settlement
  can't file them wrongly.

!!! warning "Ziffer 415 means two different things"
    On the effective form, 415 is *Vorsteuerkorrekturen* (mixed use,
    Eigenverbrauch, and the switch to the Saldosteuersatz). On the
    Saldosteuersatz form, 415 is *Korrekturen bei Übernahme im
    Meldeverfahren*.[^sss-form] quints fills only the effective form's.

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

- **`zNNN`:** each Ziffer, as a decimal string rounded to the Rappen,
  exactly as the text report prints it. `z500` is the *signed* net (the
  figure the settlement posts); `z510` carries a credit as a positive
  number.
- **`rate_rows`:** the section-II lines, with their rate class, vintage and,
  under the Saldosteuersatz, the granted rate's `label` and `activity`.
  `tax` is `net` × `rate`.
- **`bezug_rows`:** the Bezugsteuer lines (383/382), one per statutory rate.
- **`output_vat`:** what the books posted to OutputVAT, which the settlement
  clears.
- **`violations`:** anything the form can't represent.
- **`vat_method`**, **`form`**, **`liable_from`** / **`liable_to`** (the
  stretch actually covered), and **`notices`**.

Keys are only ever added, never renamed or removed.

[^ech-calc]: eCH-0217 *Spezifikation E-MWST* 2.0.0, [Kap. 6.2 and 7.5](https://www.ech.ch/sites/default/files/imce/eCH-Dossier/eCH-Dossier_PDF_Publikationen/Hauptdokument/STAN_d_DEF_2025_06_30_eCH-0217_V2.0.0_E-MWST.pdf): "Die Steuer muss vom Umsatz berechnet werden", "ohne Runden in den Zwischenschritten"; error MWST-0006 when the amount "wurde nicht korrekt berechnet". In quints: `quints.mwst._assemble`.
[^ech-activity]: eCH-0217 2.0.0, Kap. 3.2: "Für Abrechnungsperioden ab dem 01.01.2025 muss die Kumulation pro Tätigkeit erfolgen."
[^rates]: Art. 25 MWSTG, [SR 641.20](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_25). In quints: `quints.ledger.VAT_RATES`.
[^sss-rates]: Verordnung der ESTV über die Höhe der Saldosteuersätze nach Branchen und Tätigkeiten, [SR 641.202.62](https://www.fedlex.admin.ch/eli/cc/2024/500/de) (5 September 2024, in force since 1 January 2025). In quints: `quints.ledger.SALDO_RATES`.
[^sss-gross]: [MWST-Info 12 *Saldosteuersätze*](https://www.gate.estv.admin.ch/mwst-webpublikationen/public/pages/taxInfos/tableOfContent.xhtml?publicationId=1004992), Ziff. 18.1.
[^sss-form]: The ESTV's sample returns: [effective method](https://www.estv2.admin.ch/mwst/formulare/mwst-form-abr-muster-effektiv-de.pdf), [Saldosteuersatz](https://www.estv2.admin.ch/mwst/formulare/mwst-form-abr-muster-sss-de.pdf). Since 2025, returns are filed online only.
[^periods]: Art. 35 Abs. 1 and 1bis MWSTG, [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_35).
[^due]: Art. 71 Abs. 1 (the return) and Art. 86 Abs. 1 MWSTG (the payment): "Innert 60 Tagen nach Ablauf der Abrechnungsperiode". [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_86).
[^art41]: Art. 41 Abs. 2 MWSTG: "Wird das von der steuerpflichtigen Person aufgewendete Entgelt korrigiert, so ist im Zeitpunkt, in dem die Korrektur verbucht oder das korrigierte Entgelt bezahlt wird, eine Anpassung des Vorsteuerabzuges vorzunehmen." [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_41).
[^entgelt]: Art. 18 Abs. 1 MWSTG: "Der Inlandsteuer unterliegen die im Inland durch steuerpflichtige Personen gegen Entgelt erbrachten Leistungen; sie sind steuerbar, soweit dieses Gesetz keine Ausnahme vorsieht." [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_18).
