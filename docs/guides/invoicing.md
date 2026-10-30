# Invoicing

Write the invoice as one YAML file, render it, and paste the booking it
prints. quints produces the QR-bill PDF, checks it against the ledger, and
later matches the payment to it.

!!! abstract "Applies if"
    - **Legal form:** Einzelfirma, GmbH or AG. Same steps for all three.
    - **VAT status:** registered or not. quints reads it from `quints.toml`
      for the invoice's issue date.
        - *Registered:* the domestic invoice charges VAT.
        - *Not registered:* it charges and mentions none — see
          [below](#not-vat-registered).
    - **Invoices:** domestic Swiss QR-bills in CHF or EUR, and export
      invoices (SEPA/SWIFT, EU reverse charge).
    - **Not covered:** e-invoicing formats (ZUGFeRD, Peppol), credit notes as
      separate documents, instalment plans beyond
      [advances against a pro forma](#get-paid-before-you-invoice-pro-forma).

## Render a QR-bill

```bash
quints invoice invoicing/acme-2026-07.yaml
```

Writes a Swiss QR-bill PDF (domestic, or export/reverse-charge without QR
part) and cross-checks the total against your ledger: if the invoice is
already booked at a different amount, you get a conflict, not a silent
divergence. If it isn't booked yet, quints prints the draft transaction to
paste into `books/<year>.bean`. On a EUR invoice the draft's VAT leg is in
CHF at the issue date's rate (`-75.33 CHF @@ 81.00 EUR`), because the VAT
return is in CHF.

The PDF is filed the way beancount documents are filed — under the income
account's folder, date-prefixed, so `option "documents"` and Fava pick it up:

```text
documents/Income/CH/GmbH/Consulting/External/Domestic/2026-07-02.acme.INV2026014.pdf
```

The customer part is the registry key from `customers.yaml` (the slugified
name for inline customers). `--out` overrides the location when you need to.

![Rendering the two sample invoices — domestic QR-bill and EUR export — each cross-checked against the ledger](../assets/invoice.gif)

![The generated Swiss QR-bill PDF, with receipt and payment part](../assets/invoice-qr-bill.png){ width="480" }

An invoice is one YAML file:

```yaml
number: INV2026014
kind: domestic          # or export (reverse charge, no QR part)
currency: CHF
issue_date: 2026-07-02
supply: 2026-07          # the supply period — see below
customer:
  name: Acme AG
  address:
    - Bahnhofstrasse 1
    - 8001 Zürich
items:
  - description: Consulting — July
    quantity: 1
    unit_price: 1000.00
locale: de_CH
```

Issuer identity — name, address, VAT ID, bank details per currency, logo —
lives once in `invoicing/issuer.yaml`. Repeat customers can live in
`invoicing/customers.yaml` and be referenced by key (`customer: acme`).

## Supply period

`supply` says when the work was done or the goods delivered, and every invoice
needs one. It takes one of three forms:

```yaml
supply: 2026-07                              # a calendar month
supply: 2026-07-15                           # a single day
supply: {from: 2026-07-01, to: 2026-09-30}   # any period, both days included
```

It is printed under *Leistungsperiode* / *Service period* in the invoice's
locale (`Juli 2026`, `15.07.2026`, `1. Juli – 30. Sept. 2026`). On a QR-bill it
also goes into the billing information as Swico `/31/` (see
[below](#your-customers-references)), so the payer's software books the input
tax in the right VAT period without anyone retyping it.

The law asks for this date. A Swiss invoice has to state the date or period
of the supply,[^supply-ch] and an EU invoice has to state the date the supply
was made or completed if it differs from the invoice date.[^supply-eu] An
invoice that goes out on the day of the supply could skip it, but quints asks
for it every time. A stated date is never wrong, and the payer's VAT booking
relies on it.

Free text is refused. `supply: Juli 2026` fails at load, and the error names
the value to write instead (`supply: 2026-07`), so older invoice files are a
one-line fix. The ledger draft still reads `Juli 2026 invoiced`.

The supply period also sets the VAT rate: the rate in force when the supply
was made applies, not the invoice date's.[^rate-change] Without `vat.rate`, the
invoice charges the standard rate for the supply: 8.1 % from 2024, 7.7 % for
a 2023 supply invoiced later. Write `vat: {rate: 2.6}` or `{rate: 3.8}` for
the reduced or lodging rate. A rate the law doesn't set for that period is
refused at load, because VAT shown on an invoice is owed even when it is too
high.[^too-high] A supply period that spans a rate change is refused too:
invoice the part before and the part after separately.

## Bank details

One account per invoicing currency, under `bank`:

```yaml
bank:
  CHF:
    iban: CH93 0076 2011 6238 5295 7     # the account invoices are paid into
    bic: POFICHBEXXX
    bank_name: PostFinance AG, Bern      # optional
    # reference: qrr                     # see "Payment reference" below
    # qr_iban: CH57 3000 0123 0008 8901 2
    # qr_reference_id: "123456"          # the six digits your bank assigns you
  EUR:
    iban: DE89 3704 0044 0532 0130 00
    bic: COBADEFFXXX
    bank_name: Commerzbank AG, Köln
    holder: Example GmbH                 # optional — if not the issuer
```

Every IBAN is checked at load (length, national layout, mod-97 check digits)
and every BIC against ISO 9362, so a transposed digit fails before the PDF
exists rather than after it was sent.

**`bic` is mandatory for any currency you invoice abroad in.** An export
invoice refuses to render without one: it goes on the PDF as `BIC/SWIFT`,
next to the beneficiary and the bank's name, so the payer copies it instead
of looking one up — a guessed BIC is how a SEPA transfer comes back. quints
never derives it from the IBAN: that mapping lives in a bank registry that
changes, and a wrong-but-plausible BIC would be worse than none. Ask the bank
that holds the account.

To check a pair before it ships — or every account in the issuer config:

```bash
quints iban "DE89 3704 0044 0532 0130 00" --bic COBADEFFXXX
quints iban
```

It reports the country, the institution (IID) for Swiss and Liechtenstein
IBANs, and flags a BIC whose country doesn't match the IBAN's — normal for a
payment provider (a Wise EUR account is a Belgian IBAN), wrong if you pasted
another account's code. It exits non-zero when something is off, so it fits a
pre-flight check.

## Payment reference

Every invoice asks to be paid with a structured reference, so the payment
arrives carrying its own invoice id. Switzerland has two, each tied to a
different kind of bank account, and quints picks the Swiss-native one whenever
your account can carry it:

**QRR** — the Swiss QR reference, paid into a **QR-IBAN**. The payer's bank
refuses any payment to a QR-IBAN without a valid reference, so every payment
that reaches you is matchable. It is numeric — quints encodes the invoice
number so its digits stay visible at the end — and CHF-only. An account with a
`qr_iban` uses it for CHF QR-bills without further ado; add the six-digit
identification your bank assigns you, and keep the regular `iban` for CHF from
abroad:

```yaml
bank:
  CHF:
    qr_iban: CH57 3000 0123 0008 8901 2
    qr_reference_id: "123456"   # quoted: YAML reads a leading zero as octal
    iban: CH93 0076 2011 6238 5295 7
```

**SCOR** — the ISO 11649 Creditor Reference, paid into the regular **IBAN**.
`RF`, two check digits, then the invoice number itself:

```text
RF47 INV2 0260 14
```

Readable on a bank statement, quotable in a reminder email, valid on a Swiss
QR-bill in CHF and EUR *and* in a SEPA transfer. It is what an account without
a `qr_iban` uses, what every QR-bill in EUR uses, and what every export invoice
uses. Prefer it although you have a QR-IBAN? Say so:

```yaml
bank:
  CHF:
    reference: scor
    qr_iban: CH57 3000 0123 0008 8901 2
    iban: CH93 0076 2011 6238 5295 7
```

The same invoice under each scheme — the payment part is what the customer
scans or retypes. With a QR-IBAN and the bank's identification, a QR
reference whose tail is the invoice's own digits:

![Payment part paid by QR reference: account CH57 3000 0123 0008 8901 2 (a QR-IBAN), reference 12 34561 00841 17000 00202 60143](../assets/payment-part-qrr.png)

With the regular IBAN, a creditor reference that spells the invoice number out:

![Payment part paid by creditor reference: account CH93 0076 2011 6238 5295 7, reference RF47 INV2 0260 14](../assets/payment-part-scor.png)

[**Payment references**](payment-references.md) covers the whole picture — the
three types the standard defines, the exact pairing rules with their
citations, what the QR reference contains, how to choose, and how a payment
finds its way back to the invoice.

The reference is derived from the invoice number and nothing else, which is
what lets `quints import` and `quints match` credit an incoming payment to
its invoice — by the reference it quotes, the plain number, or a QR reference
decoded back into one. Two invoices never share a reference as long as their
numbers differ in a letter or digit: case and punctuation are not carried, so
`INV-014` and `INV014` would collide (the matcher then reports the payment as
ambiguous instead of guessing). A number too long to encode is refused instead
of truncated, because a truncated reference is a payment credited to somebody
else's invoice. `quints invoice` prints the reference it used:

```text
ref SCOR RF47 INV2 0260 14
```

Set `reference:` on the *invoice* only to re-issue one that went out with a
reference from elsewhere — it is validated by its check digits at load, and
refused if it doesn't match the account's scheme.

## Your customer's references

Accounts-payable departments pay against their own numbers.
`customer_reference` is their PO or order number; `references` is the long
tail — a Leitweg-ID, a CIG/CUP, a cost centre, a framework contract:

```yaml
number: INV2026014
customer: acme
customer_reference: PO-2026-118
references:
  - label: Leitweg-ID
    value: 991-12345-67
  - label: Kostenstelle
    value: KST-4711
```

Both are printed next to the invoice number and date, on domestic and export
invoices alike, with `customer_reference` labelled in the invoice's language
("Ihre Referenz" / "Your reference" / "Su referencia") and the extras labelled
exactly as you wrote them. `customer_reference` also lands in the ledger draft
as `customer_reference:` metadata, so the books can be searched by the number
the customer quotes.

On a QR-bill it additionally travels in the structured *billing information*
element, in Swico's S1 syntax:[^s1]

```text
//S1/10/INV2026014/11/260702/20/PO-2026-118/30/267359056/31/260701260731/32/8.1/40/0:30
```

That is the invoice number, its date, your customer's reference, your UID, the
supply period (`/31/`, first and last day as `YYMMDD`; a single day is one
date), the VAT rate and the payment terms — machine-readable for the payer's
accounts-payable software. Banks do not forward it with the payment; what comes
back to you is the reference and the unstructured message, which carries the
invoice number.

## Branding

The PDF's typography and palette come from the `brand` block in
`issuer.yaml`. Three font roles are separate on purpose:

```yaml
brand:
  font: Geist              # body copy
  font_display: Newsreader # the title
  font_mono: Geist Mono    # every figure, IBAN and reference
  font_dir: invoicing/fonts # optional — ship your own families
  accent: "#6b1f4a"        # title, amount due, leading rules
  logo: invoicing/wordmark.svg
  logo_height: 9           # mm
```

`font_mono` is what keeps amount columns aligned on the digit — point it at
any monospace (or a face with tabular figures; the template requests them
either way). All the values above are also the defaults: the three families
ship with quints, so an issuer that configures nothing still renders
identically on every machine. Set `font_dir` to use families of your own;
that also switches machine-installed fonts off, so a designer's locally
installed variable font can never change how the same invoice renders
elsewhere. The full token list (ink, subtle, rule, panel, display weight and
stretch) is in the [issuer schema](https://sealambda.github.io/quints/schema/issuer.schema.json).

## Not VAT-registered

A business that isn't registered must not show VAT on its invoices. If it
does, it owes the VAT it showed.[^art27] quints takes the status from
`quints.toml` for the invoice's **issue date**: `vat_registered = false`, or
a date before `vat_registered_since` or after `vat_registered_until`. For such
an invoice, quints leaves out:

- the VAT row: the total is the net amount;
- the Swiss export note on an export invoice;
- the VAT number (`/30/`) and rate (`/32/`) in the QR-bill's billing
  information;
- the OutputVAT leg in the ledger draft.

The issuer's `vat_id` has to match:

- **Registered:** the VAT number is required on every invoice, e.g.
  `CHE-123.456.789 MWST`.[^vat-id]
- **Not registered:** leave `vat_id` out, or give the bare UID without the
  `MWST`/`TVA`/`IVA` suffix. quints refuses to render an invoice from an
  unregistered issuer whose `vat_id` reads as a VAT number.

The day registration starts, the next invoice charges VAT, with no change to
the invoice files. Invoices dated before that day keep rendering without VAT,
so a re-render reproduces what was sent.

## Foreign invoices

```bash
quints invoice invoicing/globex-2026-08.yaml
```

An export invoice (`kind: export`) renders without a QR part. It shows the
full SEPA/international payment instruction instead (beneficiary, IBAN,
BIC/SWIFT, bank, reference) and the Swiss note that the place of supply is
abroad.[^place] It defaults to the EU B2B reverse-charge note, which requires
the customer's VAT number in the registry.[^reverse] Set
`reverse_charge: false` for customers outside a reverse-charge regime (e.g.
the US). The currency's account needs both a regular `iban` (a QR-IBAN can't
receive an ordinary credit transfer) and a `bic`.

![The generated export invoice PDF — no QR part, SEPA IBAN and reverse-charge note instead](../assets/invoice-export.png){ width="480" }

A project scaffolded with `quints init --samples` includes both flavours:
`invoicing/acme-<year>-07.yaml` (domestic QR-bill) and
`invoicing/globex-<year>-08.yaml` (EUR export), each tied to a booking in the
sample quarter so the cross-check reconciles.

## Get paid before you invoice: pro forma

A customer pays part of a project now but wants the invoice dated later, for
example in their next financial year. Send a **pro forma**: a request for an
advance payment, not an invoice. Book the payment as a customer advance when it
arrives. The invoice then follows with the supply, shows VAT on the whole of
it, subtracts the advance, and asks for the rest.

!!! abstract "Applies if"
    - **Legal form:** any. Same steps.
    - **Customer:** domestic or abroad, with or without reverse charge.
    - **VAT status:**
        - *Not registered:* no VAT on the advance or the invoice.
        - *Effective method:* domestic advance → its VAT is owed in the
          period the money arrives, not the invoice's.[^advance-vat]
          Advance from abroad → declared in Ziffer 200 and deducted under
          221 in that period, with no tax.[^advance-221]
        - *Saldosteuersatz:* the advance is gross Entgelt in the period it
          arrives.
    - **Filing period:** any. The advance lands in the period of the payment,
      the rest in the period of the invoice.

Either way the advance goes into the return for the period it is paid in, even
though no invoice exists yet. The invoice's period declares only the remainder.
quints handles that through two accounts in `quints.toml`, both KMU 2030
*Erhaltene Anzahlungen*:

```toml
[accounts]
advances_domestic = "Liabilities:CH:GmbH:Advances:Domestic"
advances_export = "Liabilities:CH:GmbH:Advances:Export"
```

Open them in `accounts.bean` (a project scaffolded by `quints init` already has them):

```beancount
2026-01-01 open Liabilities:CH:GmbH:Advances:Domestic
  kmu: "2030"  ; Advances received
2026-01-01 open Liabilities:CH:GmbH:Advances:Export
  kmu: "2030"  ; Advances received
```

### 1. Render the pro forma

Give it its own number series, so its payment reference never collides with
an invoice's. Write the advance as the line item, and the supply period of the
work it pays for:

```yaml
number: PF2026001
proforma: true
kind: export
currency: EUR
issue_date: 2026-11-02
supply: {from: 2027-01-01, to: 2027-03-31}
customer: globex
items:
  - description: Advance 50 % — Project Atlas
    quantity: 1
    unit_price: 10000.00
```

<!-- no-test: the whole pro forma flow runs in tests/test_proforma.py::test_cli_pro_forma_to_invoice_end_to_end -->
```bash
quints invoice invoicing/globex-pf2026001.yaml
```

The PDF is titled *Proforma-Rechnung* / *Pro forma invoice*, never
*Rechnung*. It carries a note that it is a request for an advance and not a
basis for deducting input tax. It still asks to be paid, with a QR-bill or
the SEPA block and a reference derived from `PF2026001`. Swiss law treats
any document that bills a supply as an invoice, whatever its
title.[^rechnung] So a pro forma carries no Swico billing information
either: the payer's accounts-payable software would book that as an
invoice. It is filed under the advances account:

```text
documents/Liabilities/CH/GmbH/Advances/Export/2026-11-02.globex.PF2026001.pdf
```

Nothing is booked yet. quints prints the legs the payment will need.

### 2. Book the payment when it arrives

The bank import drafts the cash leg (the payment quotes `RF… PF2026001`).
Complete it with the legs `quints invoice` printed for the pro forma, dated
the day the money arrived:

```beancount
2026-11-20 * "Globex Ltd" "Advance on pro forma PF2026001" ^PF2026001
    proforma: "PF2026001"
    document: "2026-11-02.globex.PF2026001.pdf"
    Assets:CH:GmbH:Current:Wise:EUR           10000.00 EUR
    Liabilities:CH:GmbH:Advances:Export      -10000.00 EUR
```

A domestic advance also splits out its VAT: CHF 10'810 received is
`Advances:Domestic -10000.00` and `OutputVAT -810.00`. In a foreign
currency that VAT is owed in CHF at the rate of the day the money
arrived,[^advance-rate] which the pro forma cannot know: its OutputVAT leg is
marked `TODO`. Convert it with `quints vat convert <vat> EUR <day received>`.
If the customer paid a different amount, split that amount: VAT = amount ×
rate ÷ (100 + rate). Run `quints invoice` on the pro forma again and it
reports *Advance received*, or *received X of Y* while it is paid in part.

### 3. Invoice the supply and credit the advance

The invoice lists the whole supply and the advances it credits, each with
what was actually received (VAT included):

```yaml
number: INV2027001
kind: export
currency: EUR
issue_date: 2027-01-29
supply: {from: 2027-01-01, to: 2027-03-31}
customer: globex
items:
  - description: Project Atlas
    quantity: 1
    unit_price: 20000.00
advances:
  - proforma: PF2026001
    received: 2026-11-20
    amount: 10000.00
```

A pro forma paid in instalments is listed once per instalment; the
cross-check adds them up.

The PDF shows the invoice total with VAT on the whole supply, then *Advance
PF2026001 received 20.11.2026*, then the **amount due**. The QR-bill asks
for that amount only; an invoice the advances cover in full asks for nothing
and carries no payment part. The customer never got an invoice for the
advance, so they deduct the full input tax on this one. The draft books the
whole income, releases the advance as its receipt booked it, and leaves the
rest as receivable:

```beancount
2027-01-29 * "Globex Ltd" "1 Jan – 31 Mar 2027 invoiced" ^INV2027001
    invoice: "INV2027001"
    Income:CH:GmbH:Consulting:External:Export     -10000.00 EUR
    Income:CH:GmbH:Consulting:External:Export     -10000.00 EUR @@ 9300.00 CHF
    Assets:CH:GmbH:Receivable:Trade                10000.00 EUR
    Liabilities:CH:GmbH:Advances:Export            10000.00 EUR @@ 9300.00 CHF
      proforma: "PF2026001"
```

In a foreign currency the advance leaves at the CHF it came in at (here EUR
10'000 at 0.93 in November), and the share of income it becomes is valued at
that too. The advances account then clears in CHF as well as in EUR; the rest
of the income is valued at the invoice date.

On a domestic invoice the OutputVAT leg is only the VAT not yet declared:
CHF 1'620 on the whole supply, minus the 810 the advance declared. The
cross-check confirms each advance against its receipts in the ledger and
reports one booked at another amount, on the other advances account, or with
VAT that does not match the invoice's rate — any of them would leave
OutputVAT or the advances account out of balance.

### 4. File the two returns

<!-- no-test: needs the bookings above -->
```bash
quints vat report -p 2026-Q4
quints vat report -p 2027-Q1
```

Q4 2026 declares the advance, Q1 2027 the rest. The advance is settled by the
work, not in money, so it is not revalued at year end: `quints fx revalue`
skips it, and the balance sheet carries it at the CHF it was received at
until the invoice releases it.

## Editor validation

The invoice, issuer, and customers files have JSON Schemas, published with
this site at
[`/quints/schema/`](https://sealambda.github.io/quints/schema/invoice.schema.json).
Scaffolded YAMLs already carry the matching `yaml-language-server: $schema=`
modeline, so schema-aware editors (and agents) validate fields as you type.
To work offline:

```bash
quints schema
```

writes the same schemas to `invoicing/schema/`.

## Who owes you

```bash
quints receivables
```

Open invoices against `Receivable:Trade`, grouped by invoice id, aged by due
date. An invoice disappears from the list when the payment leg is booked with
the same `^invoice-id` link. A receivable posting with no invoice id (an
opening balance, a payment booked without the link) is listed below the
table, so the open total never quietly differs from the account balance.

Each invoice shows in its own currency; below the per-currency totals a
consolidated total converts everything at the latest rate in your price file
(`≈ Total CHF`). It consolidates in the operating currency by default —
`--in EUR` picks another. A currency with no rate is excluded and called out;
`quints prices sync` fixes that.

![quints receivables lists open invoices aged by due date](../assets/receivables.gif)

## What quints doesn't do here

- **Send the invoice.** quints writes the PDF; mailing it is yours.
- **E-invoicing formats** (ZUGFeRD/Factur-X, Peppol, EDIFACT).
- **Several VAT rates on one invoice.** The rate is per invoice (`vat.rate`),
  not per line.
- **Derive a BIC from an IBAN.** Ask the bank. A guessed BIC is worse than
  none.
- **Match a pro forma's payment on import.** It isn't booked until paid, so
  `quints import` has no open item to match it to and leaves the draft `!`.
  Complete it with the legs `quints invoice` printed for the pro forma.
- **Carry an advance's CHF value over to the invoice.** A foreign-currency
  advance is declared at the rate on the day it arrived, and released at the
  invoice date's rate. Ziffer 200/221 in the two periods can differ by that
  FX difference. It is tax-neutral for exports; note it in the annual
  turnover reconciliation.

[^supply-ch]: Art. 26 Abs. 2 Bst. c MWSTG, [SR 641.20](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_26).
[^supply-eu]: Art. 226 point (7) of the VAT Directive 2006/112/EC: "the date on which the supply … was made or completed … in so far as that date can be determined and differs from the date of issue". [EUR-Lex](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:02006L0112-20250414).
[^rate-change]: Art. 115 Abs. 1 MWSTG applies Art. 112 to a rate change: "Für Leistungen, die vor Inkrafttreten … erbracht worden sind, … gilt das bisherige Recht", and a supply made partly before and partly after is taxed "für diesen Teil" under each. [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_115). The rates themselves: Art. 25 MWSTG.
[^too-high]: Art. 27 Abs. 2 MWSTG: whoever "für eine Leistung eine zu hohe Steuer ausweist, schuldet die ausgewiesene Steuer". [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_27).
[^art27]: Art. 27 Abs. 1 and 2 MWSTG: "Wer nicht im Register der steuerpflichtigen Personen eingetragen ist …, darf in Rechnungen nicht auf die Steuer hinweisen"; who shows it anyway "schuldet die ausgewiesene Steuer". [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_27).
[^vat-id]: Art. 26 Abs. 2 Bst. a MWSTG: the invoice names the issuer "sowie die Nummer, unter der er oder sie eingetragen ist". The `MWST` suffix format is the one in [MWST-Info 16, Ziff. 2.2](https://www.gate.estv.admin.ch/mwst-webpublikationen/public/pages/taxInfos/cipherDisplay.xhtml?publicationId=1002536&componentId=1002626).
[^place]: Art. 8 Abs. 1 MWSTG (place of supply of services: where the recipient has its seat), [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_8).
[^reverse]: Art. 44 and 196 of the VAT Directive (place of supply and reverse charge for B2B services), Art. 226 points (4) and (11a) (the customer's VAT number and the "Reverse charge" mention). [EUR-Lex](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:02006L0112-20250414).
[^advance-vat]: Art. 40 Abs. 1 Bst. c MWSTG: the tax arises "mit der Vereinnahmung des Entgelts bei Vorauszahlungen für nicht von der Steuer befreite Leistungen" (in force since 1 Jan. 2025). [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_40). MWST-Info 16 *Buchführung und Rechnungsstellung*, Ziff. 1.6.4: "Anzahlungen von Kunden müssen beim Erhalt als Umsatz versteuert werden." [ESTV](https://www.gate.estv.admin.ch/mwst-webpublikationen/public/pages/taxInfos/tableOfContent.xhtml?publicationId=1002536)
[^advance-rate]: Art. 45 MWSTV: a consideration in a foreign currency is converted to CHF at the time the tax claim arises, which for an advance is the day it is received (Art. 40 Abs. 1 Bst. c MWSTG). [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/828/de#art_45)
[^advance-221]: MWST-Info 15 *Abrechnung und Steuerentrichtung*, Ziff. 1.3: under agreed consideration, Ziffer 200 includes "Sämtliche Zahlungseingänge, die nicht in Rechnung gestellt wurden", and Ziffer 221 "Zahlungseingänge aus erbrachten oder zukünftigen Leistungen, die nicht in Rechnung gestellt wurden und deren Ort der Leistungserbringung im Ausland liegt". [ESTV](https://www.gate.estv.admin.ch/mwst-webpublikationen/public/pages/taxInfos/tableOfContent.xhtml?publicationId=1013189)
[^rechnung]: Art. 3 Bst. k MWSTG: "Rechnung: jedes Dokument, mit dem gegenüber einer Drittperson über das Entgelt für eine Leistung abgerechnet wird, gleichgültig, wie dieses Dokument im Geschäftsverkehr bezeichnet wird". [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_3)
[^s1]: Swico, [*Syntaxdefinition der Rechnungsinformationen (S1) bei der QR-Rechnung*, Version 1.2, 23.11.2018](https://www.swico.ch/media/filer_public/20/76/2076a19a-a017-438a-bc9a-73b1a5980d00/v2_qr-bill-s1-syntax-de.pdf). SIX has taken over the definition and reproduces it in Anhang D of the [Swiss Implementation Guidelines QR-bill 2.4](https://www.six-group.com/dam/download/banking-services/standardization/qr-bill/ig-qr-bill-v2.4-de.pdf).
