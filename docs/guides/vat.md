# File your VAT return

Every VAT period ends the same way: compute the return, check it, upload it
to the ESTV portal, close the period in the books, and pay. quints does the
computing, the upload file and the closing entry. Submitting and paying are
yours.

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

    Under the effective method:

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

    Under the Saldosteuersatz method (here on a project scaffolded for it):

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
  Saldosteuersatz. A **ledger error** is listed too: a mistyped `include`,
  a syntax error or a failing `balance` assertion. Beancount drops what it
  can't read, so the Ziffern are short until you fix it (`quints check`
  shows each error). Fix each one in the books, then run the report again.
  `quints check` fails on the Prüfung list of every period not settled yet,
  so CI catches it before you file.
- **!** marks a notice from the VAT timeline: the first return after
  registering, the last one before a switch of method, the final one before
  deregistering. Each names the correction the law asks for, its article,
  and the tag that files it under the right Ziffer.

Where a booking lands, and how to move it with a `mwst:` tag, is set out in
[How quints fills the VAT return](../reference/vat-rules.md).

## 3. File it in the ESTV portal

```bash
quints vat export -p 2026-Q3 --output-file vat-2026-Q3.xml
```

It writes the return as an eCH-0217 file, the format the portal takes by
upload.[^ech] In the ESTV ePortal,[^portal] open *MWST abrechnen*, upload the
file for the period, compare what the portal shows with the report, and
submit. quints submits nothing. The return and the payment are both due
**60 days after the period ends**.[^due]

- **Your UID** is read from `vat_id` in `invoicing/issuer.yaml`, your name
  from `quints.toml`. The portal rejects a file whose UID, method or period
  isn't the one it holds for you.
- **Errors block the export.** While *Prüfung* lists an error, quints writes
  no file: its figures would be short.
- **A correction** of a return you already filed: add `--correction`. The
  file is then a Korrekturabrechnung, complete like the first one, and
  replaces it.[^ech-types]

=== "Effective method"

    Under the effective method, the file carries what the report shows:
    turnover net of VAT per rate, Bezugsteuer, and the input VAT of Ziffern
    400–420.

=== "Saldosteuersatz"

    Under the Saldosteuersatz, the portal takes turnover **per activity**
    for periods from 2025 on.[^ech-activity] Give each granted rate the
    5-character activity code the portal shows under
    *Abrechnungsmodalitäten*:

    ```toml
    [[vat.saldo]]
    rate = 6.2
    activity = "12345"
    ```

    Without it, `quints vat export` stops and names the rate.

You can still copy the Ziffern in by hand instead. The portal computes the
tax from the turnover you enter, as quints does.

## 4. Close the period in the books

```bash
quints vat settle -p 2026-Q3
```

It prints the same report, followed by the settlement transaction and its
balance assertions. Paste them into `books/<year>.bean`. The entry moves
OutputVAT, InputVAT and Bezugsteuer into `PayableVAT`, so the next period
starts at zero. It carries the `^VAT-2026-Q3` link and a `due:` date.

Under the effective method the entry can carry one more leg of a few Rappen,
to the `rounding_income` account. The return's tax is turnover × rate,
rounded once,[^ech-calc] while your invoices rounded their VAT one by one. The
leg clears what that leaves on OutputVAT.

Under the Saldosteuersatz there is one more leg too. The entry pays the ESTV the
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
`^VAT-<period>` link, and the period drops off the list. The statement
importers add the link for you when a payee rule routes the ESTV debit to
`PayableVAT` and the amount clears exactly one open period; two periods
owing the same amount leave the draft unlinked for you to decide. A credit
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
quints vat export -p 2026-Q3
quints vat settle -p 2026-Q3 --json
quints vat status --json
```

- **`vat export`** prints the eCH-0217 XML on stdout, or exits 1 with the
  problems on stderr.
- **`zNNN`:** every Ziffer, as a decimal string.
- **`rate_rows`** and **`bezug_rows`:** the section-II lines.
- **`violations`:** the *Prüfung* list.
- **`notices`:** the timeline notices.
- **`liable_from`** / **`liable_to`:** the stretch of the period actually
  covered.

Keys are only ever added. `vat status --json` also says how the entity files
today (`vat_method`, `period`). The full contract is on the
[reference page](../reference/vat-rules.md#json).

## What quints doesn't do here

- **Submit or pay.** quints writes the file. You upload and submit it in the
  ESTV ePortal, and the payment leaves your bank. The ESTV has no interface
  for software to file directly yet.[^ech]
- **The Jahresabstimmung and the Schlussabrechnung.** The annual
  reconciliation (Art. 72 MWSTG) carries only differences, and the final
  return on deregistering has its own form in the portal.[^ech-types] Enter
  both there by hand, from `quints vat report`.
- **Saldosteuersatz returns before 2025:** the export leaves out the
  deductions of Ziffern 470 and 471 (forms 1050, 1055 and 1056), which
  quints doesn't compute.
- **A description for Ziffer 280.** The file calls it "Diverses". Edit it in
  the portal after the upload.
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
[^ech]: eCH-0217 *Spezifikation E-MWST*, version 2.0.0, approved 2025-09-23: [standard (PDF)](https://www.ech.ch/sites/default/files/imce/eCH-Dossier/eCH-Dossier_PDF_Publikationen/Hauptdokument/STAN_d_DEF_2025_06_30_eCH-0217_V2.0.0_E-MWST.pdf). Kap. 2.1: the portal takes the XML by manual upload; a machine-to-machine interface is planned.
[^ech-types]: eCH-0217 2.0.0, Kap. 3.1: a Korrekturabrechnung "wird wie die Ersteinreichung vollständig ausgefüllt und ersetzt die Ersteinreichung"; the Jahresabstimmung "enthält … nur die festgestellten Differenzen". Kap. 7.3 for the Schlussabrechnung.
[^ech-activity]: eCH-0217 2.0.0, Kap. 3.2 and 5.3.11: "Für Abrechnungsperioden ab dem 01.01.2025 muss die Kumulation pro Tätigkeit erfolgen"; "Es dürfen nur bewilligte activityId übermittelt werden."
[^ech-calc]: eCH-0217 2.0.0, Kap. 6.2.1: "Die Steuer muss vom Umsatz berechnet werden" and "auf zwei Nachkommastellen ohne Runden in den Zwischenschritten"; a difference to the tax account "muss entweder im ERP-System automatisiert … oder mittels manueller Buchungen bereinigt werden".
[^portal]: "Ab dem 1. Januar 2025 müssen alle MWST-pflichtigen Unternehmen die MWST online via ePortal abrechnen." [ESTV, MWST online abrechnen](https://www.estv.admin.ch/de/mwst-online-abrechnen); the [ePortal](https://estvportal.estv.admin.ch).
[^due]: Art. 71 Abs. 1 and Art. 86 Abs. 1 MWSTG: "Innert 60 Tagen nach Ablauf der Abrechnungsperiode". [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_86). For the final return, the 60 days run from the end of liability (Art. 71 Abs. 2).
[^fx]: Art. 45 MWSTV: the monthly average or the daily selling rate published by the ESTV, the choice kept for at least a tax period. [SR 641.201](https://www.fedlex.admin.ch/eli/cc/2009/828/de#art_45); [MWST-Info 07, Ziff. 1.3.2](https://www.gate.estv.admin.ch/mwst-webpublikationen/public/pages/taxInfos/tableOfContent.xhtml?publicationId=1007607&lang=de). See [FX rates](fx.md).
[^bezug]: Art. 45 MWSTG; an entity that isn't registered owes it only above CHF 10'000 a year (Abs. 2 Bst. b). [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_45).
