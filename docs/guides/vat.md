# File your VAT return

Every VAT period ends the same way: compute the return, check it, enter it in
the ESTV portal, close the period in the books, and pay. quints does the
computing and the closing entry. Filing and paying are yours.

!!! abstract "Applies if"
    - **Legal form:** Einzelfirma, GmbH or AG. Nothing on this page differs
      between them.
    - **VAT status:** registered for the period you're filing. If you aren't
      registered, or aren't sure, start with
      [Register, switch method, deregister](vat-registration.md).
    - **Method:** the effective method or the Saldosteuersatz. The steps are
      the same; where the details differ, the tabs below show both.
    - **Period:** quarterly (the effective method's default), half-yearly
      (the Saldosteuersatz's), or annual on request. quints reads which one
      applies from `quints.toml`.

## 1. Compute the return

=== "Effective method"

    ```bash
    quints vat report -p 2026-Q3
    ```

    It prints the return Ziffer by Ziffer, in the order the ePortal asks for
    them:

    - **200–299:** turnover and its deductions.
    - **Rate rows:** 303/313/343 at 8.1/2.6/3.8 %, plus 383 for Bezugsteuer.
    - **400–479:** input VAT.
    - **500 or 510:** what you owe, or what you're owed.

    ![The full MWST report on the sample quarter — the Ziffern plus the Vorsteuer, Bezugsteuer, and revenue detail tables](../assets/mwst.gif)

=== "Saldosteuersatz"

    ```bash
    quints init jane-books --name "Jane Doe" --legal-form einzelfirma --vat-method saldo --saldo-rate 6.2 --samples --yes
    cd jane-books
    quints vat report -p 2026-H2
    cd ..
    ```

    The same 200–299, but every figure **includes VAT**. Section II is short:

    - **323:** gross turnover × each rate the ESTV granted you.
    - **383:** Bezugsteuer at the statutory rate.
    - **500 or 510:** what you owe, or what you're owed.

    There is no input-VAT block: the rate already accounts for input tax, so
    purchases are booked gross.[^sss] Your invoices still charge customers
    the statutory 8.1 / 2.6 / 3.8 %.

`-p` takes a quarter (`2026-Q3`), a half-year (`2026-H2`) or a year (`2026`);
`--from/--to` takes any range. The report is computed under the method and
rates in force for that period. If you switched method or registered
mid-period, quints picks the phase that applies, or refuses a range that
spans a switch.

## 2. Check what the report flags

Two lists appear below the Ziffern when there is something to act on:

- **Prüfung** is the list of bookings the form can't represent. Examples: a
  sale whose VAT doesn't match net × rate (the old rate applied by habit, a
  missing VAT leg), an unknown `mwst:` tag, or InputVAT under the
  Saldosteuersatz. Fix each one in the books, then run the report again.
- **!** marks a notice from the VAT timeline: the first return after
  registering, the last one before a switch of method, the final one before
  deregistering. Each names the correction the law asks for, its article,
  and the tag that files it under the right Ziffer.

Where a booking lands, and how to move it with a `mwst:` tag, is set out in
[How quints fills the VAT return](../reference/vat-rules.md).

## 3. Enter it in the ESTV portal

Copy the Ziffern into the ESTV ePortal.[^portal] quints files nothing. The
return and the payment are both due **60 days after the period ends**.[^due]

## 4. Close the period in the books

```bash
quints vat settle -p 2026-Q3
```

It prints the same report, followed by the settlement transaction and its
balance assertions. Paste them into `books/<year>.bean`. The entry moves
OutputVAT, InputVAT and Bezugsteuer into `PayableVAT`, so the next period
starts at zero. It carries the `^VAT-2026-Q3` link and a `due:` date.

Under the Saldosteuersatz there is one more leg. The entry pays the ESTV the
Saldosteuersatz on the gross turnover and books the rest of the VAT your
invoices collected to the `saldo_difference` income account. That gap is
what the method pays you for not deducting input tax.

```beancount
2026-12-31 * "2026-H2 VAT Settlement" ^VAT-2026-H2
    due: 2027-03-01
    Liabilities:CH:Einzelfirma:Tax:PayableVAT    -74.55 CHF
    Liabilities:CH:Einzelfirma:Tax:OutputVAT      81.00 CHF
    Liabilities:CH:Einzelfirma:Tax:Bezugsteuer     7.53 CHF
    Income:CH:Einzelfirma:VAT:SaldoDifference    -13.98 CHF
```

Then run `quints check`. The balance assertions prove the accounts were
emptied.

## 5. Pay, and keep track

```bash
quints vat status
```

It lists what has been settled but not yet paid, with due dates. When the
payment leaves the bank, book it against `PayableVAT` with the same
`^VAT-<period>` link, and the period drops off the list. A credit
(Ziffer 510) shows as a negative amount until the ESTV refunds or offsets
it.

`quints close check` fails the year until every period is settled, and warns
while one is settled but unpaid ([Close the year](year-end.md)).

## Foreign-currency VAT

VAT is converted to CHF at the rate the ESTV publishes. quints uses the
daily rate from `prices.bean`.[^fx] Don't compute it by hand:

```bash
quints vat convert 7.53 EUR 2026-08-01                     # CHF InputVAT posting to paste
quints vat convert 100 EUR 2026-08-01 --bezugsteuer        # reverse charge: InputVAT + Bezugsteuer pair
quints vat convert 100 EUR 2026-08-01 --net --rate reduced # 2.6 % instead of 8.1 %
```

`--bezugsteuer` is for services bought from abroad, typically a foreign SaaS
invoice.[^bezug] The emitted block carries the right `mwst:` tag for its rate,
and its legs depend on the method on the invoice date:

- **Effective method:** the pair nets to zero when you can deduct the input
  VAT in full, but it still has to be declared (383 and 400/405).
- **Saldosteuersatz:** it debits the Bezugsteuer *expense* account, since the
  tax is a cost.
- **Before registration:** it prints a note instead of postings, because
  Bezugsteuer is then owed only above CHF 10'000 a year.

## For agents

```bash
quints vat report -p 2026-Q3 --json
quints vat settle -p 2026-Q3 --json
quints vat status --json
```

- **`zNNN`:** every Ziffer, as a decimal string.
- **`rate_rows`:** the section-II lines.
- **`violations`:** the *Prüfung* list.
- **`notices`:** the timeline notices.
- **`liable_from`** / **`liable_to`:** the stretch of the period actually
  covered.

Keys are only ever added. `vat status --json` also says how the entity files
today (`vat_method`, `period`). The full contract is on the
[reference page](../reference/vat-rules.md#json).

## What quints doesn't do here

- **File or pay.** The return goes into the ESTV ePortal by hand, and the
  payment leaves your bank.
- **Value corrections.** Einlageentsteuerung (410), Eigenverbrauch and
  Vorsteuerkorrekturen (415) depend on what you still own and its current
  value. The notices say when one is due. You book the amount with the
  matching tag.
- **The Saldosteuersatz form's Ziffer 415** (takeover in the
  Meldeverfahren), and the subtotal **379** both online forms show; the
  report skips it.
- **Collected-revenue accounting** (vereinnahmte Entgelte), and a business
  year other than the calendar year. quints computes on agreed Entgelte by
  transaction date.
- **The Pauschalsteuersatz**, which is for public bodies and associations
  only.

[^sss]: Art. 37 MWSTG, [SR 641.20](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_37); [MWST-Info 12 *Saldosteuersätze*](https://www.gate.estv.admin.ch/mwst-webpublikationen/public/pages/taxInfos/tableOfContent.xhtml?publicationId=1004992). The ESTV's sample returns: [effective method](https://www.estv2.admin.ch/mwst/formulare/mwst-form-abr-muster-effektiv-de.pdf), [Saldosteuersatz](https://www.estv2.admin.ch/mwst/formulare/mwst-form-abr-muster-sss-de.pdf).
[^portal]: "Ab dem 1. Januar 2025 müssen alle MWST-pflichtigen Unternehmen die MWST online via ePortal abrechnen." [ESTV, MWST online abrechnen](https://www.estv.admin.ch/de/mwst-online-abrechnen); the [ePortal](https://estvportal.estv.admin.ch).
[^due]: Art. 71 Abs. 1 and Art. 86 Abs. 1 MWSTG: "Innert 60 Tagen nach Ablauf der Abrechnungsperiode". [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_86). For the final return, the 60 days run from the end of liability (Art. 71 Abs. 2).
[^fx]: Art. 45 MWSTV: the monthly average or the daily selling rate published by the ESTV, the choice kept for at least a tax period. [SR 641.201](https://www.fedlex.admin.ch/eli/cc/2009/828/de#art_45); [MWST-Info 07, Ziff. 1.3.2](https://www.gate.estv.admin.ch/mwst-webpublikationen/public/pages/taxInfos/tableOfContent.xhtml?publicationId=1007607&lang=de). See [FX rates](fx.md).
[^bezug]: Art. 45 MWSTG; an entity that isn't registered owes it only above CHF 10'000 a year (Abs. 2 Bst. b). [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_45).
