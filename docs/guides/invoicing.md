# Invoicing

Write the invoice as a YAML file, render it, and paste the booking it prints.
quints writes the QR-bill PDF, checks it against the ledger, and later
matches the payment to it.

!!! abstract "Applies if"
    - **Legal form:** Einzelfirma, GmbH or AG. Same steps for all three.
    - **VAT status:** registered or not. quints reads it from `quints.toml`
      for the invoice's issue date.
        - *Registered:* the domestic invoice charges VAT.
        - *Not registered:* it charges and mentions none — see
          [below](#not-vat-registered).
    - **Invoices:** domestic Swiss QR-bills in CHF or EUR, and export
      invoices for services to a customer abroad (SEPA/SWIFT, EU reverse
      charge).
    - **Not covered:** e-invoicing networks and national formats (Peppol,
      FatturaPA, Verifactu); instalment plans beyond
      [advances against a pro forma](pro-forma.md); exported goods, and
      services taxed in Switzerland although the customer is abroad (see
      [below](#foreign-invoices)).

## Write the invoice file

```yaml
number: INV2026014
kind: domestic          # or export (reverse charge; EPC QR code in EUR)
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

- **Issuer:** name, address, VAT ID, bank accounts and logo live once in
  `invoicing/issuer.yaml`.
- **Customers:** repeat customers go in `invoicing/customers.yaml` and are
  referenced by key (`customer: acme`).
- **Files:** invoices go in `invoicing/invoices/`, the logo and any fonts in
  `invoicing/brand/`.
- **`locale`:** the language, and the number and date formats. German,
  French, Italian, English and Spanish are supported (`de_CH`, `fr_CH`,
  `it_CH`, `en_GB`, `es_ES`, …). Any other language is refused at load.

## Render a QR-bill

```bash
quints invoice invoicing/invoices/acme-2026-07.yaml
```

A domestic invoice renders as a Swiss QR-bill. An export invoice prints its
SEPA payment details, plus an EPC QR code when it is in EUR. The total is
cross-checked against the ledger:

- **Booked at another amount:** a conflict.
- **Not booked yet:** quints prints the draft transaction to paste into
  `books/<year>.bean`. On a EUR invoice the draft's VAT leg is in CHF at the
  issue date's rate (`-75.33 CHF @@ 81.00 EUR`), because the VAT return is
  in CHF.

`--no-verify` skips the cross-check and the draft. An issued invoice is not
rendered again either way.

Every problem that stops an invoice is reported in one run. Under `--json`
they arrive on stdout as
`{"ok": false, "errors": [{"code", "field", "message"}]}`.

The PDF is filed where beancount and Fava look for documents: under the
income account's folder, date-prefixed.

```text
documents/Income/CH/GmbH/Consulting/External/Domestic/2026-07-02.acme.INV2026014.pdf
```

The customer part is the key from `customers.yaml`, or the slugified name of
an inline customer. `--out` writes the PDF elsewhere.

![Rendering the two sample invoices — domestic QR-bill and EUR export —
each cross-checked against the ledger](../assets/invoice.gif)

![The generated Swiss QR-bill PDF, with receipt and payment
part](../assets/invoice-qr-bill.png){ width="480" }

### Issued means fixed

The draft carries the PDF's SHA-256 next to `document:`:

```text
    document: "2026-07-02.acme.INV2026014.pdf"
    sha256: "9f2c…"
```

Once that is in the books, the invoice is issued:

- `quints invoice` checks the filed PDF against the hash, reports "already
  issued" and does not render again. A later change to a template, a font or
  the customer registry cannot alter what was sent.
- `quints check` verifies every filed PDF against its hash, fails on a
  number booked twice or dated before the one preceding it, and warns about
  gaps in a series.

To correct an issued invoice under the same number (a wrong address, caught
early), add `corrected: 2026-07-04` to the file and run
`quints invoice --reissue`. The PDF states that it replaces the version of
the issue date. Book the new hash it prints; the repository history keeps
the old bytes.

A booking without `sha256:` (older than the hash) is issued all the same.
Once its PDF is filed, `quints invoice` leaves it alone and prints the line
to add, and `quints check` warns until it is there.

## Supply period

Every invoice needs a `supply`: when the work was done or the goods
delivered. It takes one of three forms:

```yaml
supply: 2026-07                              # a calendar month
supply: 2026-07-15                           # a single day
supply: {from: 2026-07-01, to: 2026-09-30}   # any period, both days included
```

Free text is refused: `supply: Juli 2026` fails at load, and the error names
the value to write (`supply: 2026-07`). Swiss law requires the date or
period of the supply on every invoice,[^supply-ch] EU law when it differs
from the invoice date.[^supply-eu] quints asks every time.

It prints as *Leistungsperiode* / *Service period* in the invoice's locale
(`Juli 2026`, `15.07.2026`, `1. Juli – 30. Sept. 2026`). On a QR-bill it
also travels as Swico `/31/` ([below](#your-customers-references)), so the
payer's software books the input tax in the right VAT period.

The supply period also sets the VAT rate: the rate in force when the supply
was made applies, not the invoice date's.[^rate-change]

- Without `vat.rate`, the invoice charges the standard rate for the supply:
  8.1 % from 2024, 7.7 % for a 2023 supply invoiced later.
- `vat: {rate: 2.6}` or `{rate: 3.8}` charges the reduced or the lodging
  rate.
- A rate the law doesn't set for that period is refused at load, because VAT
  shown on an invoice is owed even when it is too high.[^too-high]
- A supply period that spans a rate change is refused too: invoice the part
  before and the part after separately.

A line taxed at another rate than the rest carries its own:

```yaml
items:
  - {description: Workshop, quantity: 1, unit_price: 1000.00}
  - {description: Handbook, quantity: 10, unit_price: 33.33, vat_rate: 2.6}
```

VAT is computed per rate on the sum of its lines and printed as one row per
rate, with the amount it is charged on.[^per-rate] Each line names its rate,
the QR-bill carries the breakdown (Swico S1 `/32/8.1:1000;2.6:333.3`), and
the ledger draft books one income leg per rate, tagged `mwst: "reduced"` or
`"lodging"` so the VAT return files it in the right row. An invoice with
several rates cannot be a pro forma or credit advances.

### Discounts

```yaml
items:
  - {description: Workshop, quantity: 1, unit_price: 1250.00, discount: 20}
discount: {amount: 100}   # or {percent: 5}
```

A line's `discount` is a percentage off that line, printed beside its
description. The invoice's `discount` comes off the subtotal as its own row.
Both reduce the consideration, so VAT is charged on what is left.[^discount]
With several VAT rates the invoice's discount is spread over them in
proportion to their nets. The ledger draft books income net of discounts.

### Outlays you pass on

A fee paid in the customer's name and on their account (a court fee, a visa,
a domain registered for them) is neither your revenue nor part of the
consideration, as long as the invoice shows it separately.[^pass-through]

```yaml
pass_through:
  - {description: Commercial register fee, amount: 120.00}
```

The invoice lists them after the VAT rows and adds them to the total
without VAT. Book the outlay against `disbursements`
(`Assets:…:Receivable:Disbursements`, KMU 1190) when you pay it. The ledger
draft clears it from there, so it never touches income or the VAT return. A
cost you incur in your own name and recharge is an ordinary line, with VAT.

## Bank details

One account per invoicing currency, under `bank` in `issuer.yaml`:

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

Every IBAN (length, national layout, mod-97 check digits) and every BIC
(ISO 9362) is checked at load.

- **`bic` is mandatory for any currency you invoice abroad in.** An export
  invoice refuses to render without one and prints it as `BIC/SWIFT`. quints
  never derives it from the IBAN: that mapping lives in a bank registry that
  changes, and a wrong but plausible BIC is worse than none. Ask the bank.
- **A EUR export invoice also carries an EPC QR code.** The payer's banking
  app scans it and pre-fills the transfer: beneficiary, IBAN, BIC, amount
  and RF reference.[^epc] Not every bank's app reads it; the printed fields
  stay. Other currencies get no code: the standard covers euro credit
  transfers only.
- **`holder`** is the beneficiary in that code, else your company name.
  Write it as the bank holds the account, letter for letter, legal form
  included: an EU payer's bank checks the name against the IBAN before
  sending, where the payee's bank takes part.[^vop]

Check a pair, or every account in the issuer config:

```bash
quints iban "DE89 3704 0044 0532 0130 00" --bic COBADEFFXXX
quints iban
```

It reports the country and, for Swiss and Liechtenstein IBANs, the
institution (IID). A BIC whose country differs from the IBAN's is flagged:
normal for a payment provider (a Wise EUR account is a Belgian IBAN), wrong
if you pasted another account's code. It exits non-zero when something is
off.

## Payment reference

Every invoice asks to be paid with a structured reference derived from its
number, so the payment arrives carrying its own invoice id. quints picks the
scheme from the account:

| Invoice | Account | Reference |
|---|---|---|
| QR-bill in CHF | has a `qr_iban` | **QRR**, the numeric Swiss QR reference, paid into the QR-IBAN |
| QR-bill in CHF | no `qr_iban`, or `reference: scor` | **SCOR**, paid into the regular `iban` |
| QR-bill in EUR | any | **SCOR**, paid into the regular `iban` |
| Export | any | **SCOR**, paid into the regular `iban` |

SCOR is the ISO 11649 Creditor Reference: `RF`, two check digits, then the
invoice number (`RF47 INV2 0260 14`). With a QR-IBAN, add the six-digit
identification your bank assigns you, and keep the regular `iban` for CHF
from abroad:

```yaml
bank:
  CHF:
    qr_iban: CH57 3000 0123 0008 8901 2
    qr_reference_id: "123456"   # quoted: YAML reads a leading zero as octal
    iban: CH93 0076 2011 6238 5295 7
    # reference: scor           # issue SCOR although there is a QR-IBAN
```

`quints invoice` prints the reference it used:

```text
ref SCOR RF47 INV2 0260 14
```

`quints import` and `quints match` credit an incoming payment to its invoice
by that reference. Case and punctuation are not carried, so keep numbers
distinct in their letters and digits: `INV-014` and `INV014` would collide.

[Payment references](payment-references.md) has the pairing rules with their
citations, both payment parts side by side, what the QR reference contains,
how to choose, length limits, and overriding one invoice's reference.

## Your customer's references

`customer_reference` is the customer's PO or order number. `references`
takes the rest: a Leitweg-ID, a CIG/CUP, a cost centre, a framework
contract.

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

Both print next to the invoice number and date, on domestic and export
invoices: `customer_reference` labelled in the invoice's language ("Ihre
Referenz" / "Your reference" / "Su referencia"), the extras as you wrote
them. `customer_reference` also lands in the ledger draft as metadata, so
the books can be searched by the customer's number.

On a QR-bill it also travels in the *billing information* element, in
Swico's S1 syntax:[^s1]

```text
//S1/10/INV2026014/11/260702/20/PO-2026-118/30/267359056/31/260701260731/32/8.1/40/0:30
```

That is the invoice number, its date, your customer's reference, your UID,
the supply period (`/31/`, first and last day as `YYMMDD`; a single day is
one date), the VAT rate and the payment terms. Banks do not forward it with
the payment: what comes back is the reference and the unstructured message,
which carries the invoice number.

## Branding

The PDF's typography and palette come from the `brand` block in
`issuer.yaml`:

```yaml
brand:
  font: Geist              # body copy
  font_display: Newsreader # the title
  font_mono: Geist Mono    # every figure, IBAN and reference
  font_dir: invoicing/brand/fonts # optional — ship your own families
  accent: "#6b1f4a"        # title, amount due, leading rules
  logo: invoicing/brand/wordmark.svg
  logo_height: 9           # mm
  credit: true             # "Made with quints" line under the body
```

- **Defaults:** the three fonts, the accent and `credit` above. The
  families ship with quints, so an issuer that configures nothing renders
  identically on every machine. `logo` and `font_dir` have no default;
  `logo_height` defaults to 12.
- **`font_mono`** keeps amount columns aligned on the digit. Any monospace
  works, or a face with tabular figures.
- **`font_dir`** uses families of your own and switches machine-installed
  fonts off, so a locally installed font cannot change how the invoice
  renders elsewhere.
- **`credit: false`** drops the "Made with quints" line.

The full token list (ink, subtle, rule, panel, display weight and stretch)
is in the
[issuer schema](https://sealambda.github.io/quints/schema/issuer.schema.json).

## Post it in a window envelope

Printed on A4 and folded with the address outside, the invoice shows the
recipient's address through the window of a standard envelope. Set the
window's side in the `brand` block of `issuer.yaml`:

```yaml
brand:
  address_window: right # or left
  address_top: 57       # mm from the sheet's top edge to the first address line
```

Both values are the defaults. quints starts the address 132 mm from the
sheet's left edge for a right window and 25 mm for a left one, and keeps the
title and invoice details on the other side.

A sheet slides inside its envelope: an A4 sheet has 19 mm of play sideways
in a C5 and 13.5 mm up and down. What counts is the part of the sheet the
window shows wherever the sheet sits. Swiss Post asks that the address stay
fully readable when the sheet shifts, and that nothing else
show.[^post-window]

| Envelope, A4 fold | Window | Always visible, mm from the sheet's top | Address lines that fit |
| --- | --- | --- | --- |
| Swiss C5, folded once | 100 × 45 mm, right or left | 52–83.5 | 6 |
| Swiss C5/6, folded in three | 100 × 45 mm, right | 49–79 | 5 |
| Swiss C4, unfolded | 100 × 45 mm, right or left | 55–73 | 4 |
| DIN DL, folded in three | 90 × 45 mm, left | 50–84 | 6 |
| DIN C6/5, folded in three | 90 × 45 mm, left | 54–84 | 6 |
| DIN C5, folded once | 90 × 45 mm, left | 57–88.5 | 7 |
| DIN C4, unfolded | 90 × 55 mm, left | 57–85 | 6 |

The last column is for the default `address_top`, at 4.4 mm a line. The
recipient's name, a name that wraps and the country of a foreign customer
each count as a line. The window positions are the Swiss[^window-ch] and
German[^window-din] standard ones.

1. Pick your envelope's row. If your addresses run longer than it fits, or
   the envelope isn't listed, set `address_top`: the first line must start
   inside the visible range and the last one end inside it. With Swiss C5
   envelopes only, `address_top: 53` fits seven lines.
2. Print one invoice, fold it, and push the sheet into each corner of the
   envelope in turn. The whole address stays in the window every time, and
   nothing else does.

## Not VAT-registered

A business that isn't registered must not show VAT on its invoices. If it
does, it owes the VAT it showed.[^art27] quints takes the status from
`quints.toml` for the invoice's **issue date**: `vat_registered = false`, or
a date before `vat_registered_since` or after `vat_registered_until`. For
such an invoice, quints leaves out:

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
the invoice files. Invoices dated before that day keep rendering without
VAT, so a re-render reproduces what was sent.

## Foreign invoices

```bash
quints invoice invoicing/invoices/globex-2026-08.yaml
```

An export invoice (`kind: export`) renders without a QR part. It prints the
SEPA/international payment instruction instead (beneficiary, IBAN,
BIC/SWIFT, bank, reference) and the Swiss note that the place of supply is
abroad.[^place]

- **Use it for** a service whose place of supply is the customer's seat.
- **Don't use it for** exported goods, which are exempt on other grounds and
  need an export record, or for the services the law places in Switzerland
  whatever the customer's seat: work on Swiss real estate, events held here,
  restaurant services, passenger transport.[^not-export] The note would be
  wrong.
- **Reverse charge:** it defaults to the EU B2B reverse-charge note, which
  requires the customer's VAT number in the registry.[^reverse] Set
  `reverse_charge: false` for customers outside a reverse-charge regime
  (e.g. the US).
- **Bank account:** the currency's account needs a regular `iban` (a QR-IBAN
  can't receive an ordinary credit transfer) and a `bic`.

![The generated export invoice PDF — SEPA payment details with an EPC QR
code, and the reverse-charge note](../assets/invoice-export.png){ width="480" }

`quints init --samples` scaffolds both kinds:
`invoicing/invoices/acme-<year>-07.yaml` (domestic QR-bill) and
`invoicing/invoices/globex-<year>-08.yaml` (EUR export), each tied to a
booking in the sample quarter so the cross-check reconciles.

## Get paid before you invoice: pro forma

A customer who pays part of a project before the invoice is dated gets a pro
forma, and the invoice later credits the advance:
[Get paid before you invoice](pro-forma.md).

## Cancel or reduce an invoice: credit note

An issued invoice is corrected by a further document that refers to
it.[^credit-note] Copy the invoice file, give it a number from its own
series, and name the invoice it corrects:

```yaml
number: CN2026001
credit_note_for: INV2026014
issue_date: 2026-07-10
# kind, currency, customer, supply, items: as on the invoice. All lines
# cancel it; some of them, or a lower price, reduce it.
```

`quints invoice` on that file writes a PDF titled as a credit note, with
every amount negated, a reference to the invoice and no payment part. The
ledger draft reverses income and VAT per rate, and its receivable leg
carries `invoice: "INV2026014"`, so `quints receivables` nets it against the
invoice.

- **Cancelled in full:** nothing stays open. Re-invoice under a new number.
- **Already paid:** the open item turns negative. Book the refund against
  it.

Use a credit note when the customer has booked or paid the original, or when
amounts change. For a wrong address caught early, a
[reissue](#issued-means-fixed) under the same number is enough.

## The next invoice of a recurring one

```bash
quints invoice new --from invoicing/invoices/acme-2026-07.yaml --period 2026-08
```

Copies the invoice file to `invoicing/invoices/acme-2026-08.yaml` with the
next free number of its series, today's date (`--date` sets another) and the
new supply period. Everything else, comments included, stays as written. A
series that carries the year (`INV2026014`) starts again at `001` in a new
year. It never overwrites a file. quints schedules nothing: you, an agent or
cron run it, then `quints invoice` on the new file.

## Change the numbering scheme

Start the new series on a set day, a new year being the cleanest, and tell
`quints check` where its gap checks begin:

```toml
[invoice]
gap_check_from = 2027-01-01
```

Leave the old numbers alone: they are issued. Gaps are then checked only
between invoices issued from that day on. Duplicates and dates out of order
are still checked across all of them.

quints continues and checks a prefix, the year and a counter: `INV2027001`,
or one series per customer, `ACME2027001`. Swiss law prescribes no scheme,
only a document behind each booking.[^belege] The EU asks for a sequential
number in one or more series,[^sequential] which is what your EU customers'
bookkeepers expect. A number that encodes the month (`ACME202608`) is not a
counter: a second invoice that month needs a suffix, and every month without
one reads as a gap.

## Structured invoice data: Factur-X / ZUGFeRD

Every invoice and credit note PDF embeds itself as data: `factur-x.xml` in
the EN 16931 profile, which accounts-payable software in Germany, Austria
and France reads instead of the page.[^facturx] There is nothing to switch
on. The PDF is PDF/A-3a (the archival format that allows the attachment) and
PDF/UA-1, validated in CI with veraPDF. The test suite checks the XML
against the official schema and business rules.

- Every amount in the XML is one the PDF prints. Nothing is added up again.
- A pro forma carries none: software would book it as the invoice.
- Swiss VAT travels as category `S` with the Swiss rate. An export with the
  reverse-charge mention is `AE`. Any other invoice without VAT (an export
  outside a reverse-charge regime, an issuer not registered) is `O`, "not
  subject to VAT". Pass-through items are a document-level charge.
- The `unit` text is mapped to its standard code where it is a common one
  (hours, days, months, pieces, lump sum, km, kg, in the five invoice
  languages). Anything else is sent as a plain unit (`C62`).

The EU makes structured invoices mandatory for cross-border B2B between
businesses established in the EU from 1 July 2030.[^vida] That does not bind
a Swiss supplier, but EU customers will expect the format.

## Editor validation

The invoice, issuer and customers files have JSON Schemas, published at
[`/quints/schema/`](https://sealambda.github.io/quints/schema/invoice.schema.json).
Scaffolded YAMLs carry the matching `yaml-language-server: $schema=`
modeline, so schema-aware editors and agents validate fields as you type.
To work offline, write the same schemas to `invoicing/schema/`:

```bash
quints schema
```

## Who owes you

```bash
quints receivables
```

Open invoices against `Receivable:Trade`, grouped by invoice id and aged by
due date.

- An invoice leaves the list when the payment leg is booked with the same
  `^invoice-id` link.
- A receivable posting with no invoice id (an opening balance, a payment
  booked without the link) is listed below the table, so the open total
  always accounts for the whole balance.
- Each invoice shows in its own currency. A consolidated total converts
  everything at the latest rate in your price file, into the operating
  currency or the one `--in EUR` names. A currency with no rate is excluded
  and called out; `quints prices sync` fixes that.

Allocating payments, reminders and write-offs:
[Allocate customer payments](receivables.md).

![quints receivables lists open invoices aged by due date](../assets/receivables.gif)

## What quints doesn't do here

- **Send the invoice.** quints writes the PDF; mailing it is yours.
- **Keep the header out of the window in every position.** A sheet resting
  on the bottom of its envelope shows the window more of what is above the
  address: from 38.5 mm down the sheet in a Swiss C5, 34 mm in a C5/6, 28 mm
  in a C4. The hairline under the header sits 4 mm below your address block
  (32 mm down the sheet with four lines, 42 mm with seven) and can show at
  the top of the window. Behind a right-hand window, so can the last lines
  of a block longer than four lines (C4) or five (C5/6). Leave `email` or
  `phone` off the issuer to shorten it.
- **Break a long reference without hyphens or spaces.** The invoice details
  are 78 mm wide beside a right window and 70 mm beside a left one; a
  `references` value of more than 20 (right) or 16 (left) unbroken
  characters runs past that column.
- **Other e-invoicing formats and networks** (XRechnung, Peppol, FatturaPA,
  Verifactu, EDIFACT). The PDF carries Factur-X data; nothing is
  transmitted.
- **Derive a BIC from an IBAN.** Ask the bank.

[^supply-ch]: Art. 26 Abs. 2 Bst. c MWSTG, [SR 641.20](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_26).
[^supply-eu]: Art. 226 point (7) of the VAT Directive 2006/112/EC: "the date on which the supply … was made or completed … in so far as that date can be determined and differs from the date of issue". [EUR-Lex](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:02006L0112-20250414).
[^rate-change]: Art. 115 Abs. 1 MWSTG applies Art. 112 to a rate change: "Für Leistungen, die vor Inkrafttreten … erbracht worden sind, … gilt das bisherige Recht", and a supply made partly before and partly after is taxed "für diesen Teil" under each. [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_115). The rates themselves: Art. 25 MWSTG.
[^per-rate]: Art. 26 Abs. 2 Bst. f MWSTG: the invoice states "den anwendbaren Steuersatz und den vom Entgelt geschuldeten Steuerbetrag". [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_26).
[^discount]: Art. 24 Abs. 1 MWSTG: "Die Steuer wird vom tatsächlich empfangenen Entgelt berechnet." [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_24).
[^pass-through]: Art. 24 Abs. 6 Bst. b MWSTG: not part of the consideration are "Beträge, die die steuerpflichtige Person von der die Leistung empfangenden Person als Erstattung der in deren Namen und für deren Rechnung getätigten Auslagen erhält, sofern sie diese gesondert ausweist (durchlaufende Posten)". [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_24).
[^epc]: European Payments Council, [EPC069-12 v3.1](https://www.europeanpaymentscouncil.eu/sites/default/files/kb/file/2024-03/EPC069-12%20v3.1%20Quick%20Response%20Code%20-%20Guidelines%20to%20Enable%20the%20Data%20Capture%20for%20the%20Initiation%20of%20an%20SCT.pdf), *Quick Response Code: Guidelines to Enable the Data Capture for the Initiation of a SEPA Credit Transfer*, §2.2. The BIC stays mandatory for accounts outside the EEA, so a Swiss account always includes it. The guideline itself is optional for banks to implement (§1).
[^vop]: [Regulation (EU) 2024/886](https://eur-lex.europa.eu/eli/reg/2024/886/oj), Art. 5c (verification of the payee). It binds banks in the EU; a Swiss bank answers the check only if it has joined the EPC's Verification of Payee scheme — ask yours. Where it hasn't, the payer sees that no check was possible.
[^credit-note]: Art. 27 Abs. 4 MWSTG: a later correction is made by a document, addressed to the recipient, that refers to the original invoice and revokes it. MWST-Info 16, Ziff. 2.5.1. [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_27).
[^facturx]: Factur-X 1.0 / ZUGFeRD 2.x, profile EN 16931, UN/CEFACT Cross Industry Invoice syntax. [FNFE-MPE](https://fnfe-mpe.org/factur-x/), [FeRD](https://www.ferd-net.de/standards/zugferd).
[^vida]: Council Directive (EU) 2025/516 (VAT in the Digital Age). [EUR-Lex](https://eur-lex.europa.eu/eli/dir/2025/516/oj).
[^too-high]: Art. 27 Abs. 2 MWSTG: whoever "für eine Leistung eine zu hohe Steuer ausweist, schuldet die ausgewiesene Steuer". [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_27).
[^art27]: Art. 27 Abs. 1 and 2 MWSTG: "Wer nicht im Register der steuerpflichtigen Personen eingetragen ist …, darf in Rechnungen nicht auf die Steuer hinweisen"; who shows it anyway "schuldet die ausgewiesene Steuer". [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_27).
[^vat-id]: Art. 26 Abs. 2 Bst. a MWSTG: the invoice names the issuer "sowie die Nummer, unter der er oder sie eingetragen ist". The `MWST` suffix format is the one in [MWST-Info 16, Ziff. 2.2](https://www.gate.estv.admin.ch/mwst-webpublikationen/public/pages/taxInfos/cipherDisplay.xhtml?publicationId=1002536&componentId=1002626).
[^place]: Art. 8 Abs. 1 MWSTG (place of supply of services: where the recipient has its seat), [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_8).
[^not-export]: Goods: Art. 23 Abs. 2 Ziff. 1 and Abs. 4 MWSTG (exempt, proven by customs documents), declared in Ziffer 220. Services placed in Switzerland: Art. 8 Abs. 2 MWSTG. [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_8).
[^reverse]: Art. 44 and 196 of the VAT Directive (place of supply and reverse charge for B2B services), Art. 226 points (4) and (11a) (the customer's VAT number and the "Reverse charge" mention). [EUR-Lex](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:02006L0112-20250414).
[^post-window]: Die Post, *Spezifikationen Briefgestaltung von A–Z*, Ausgabe Juli 2026, «Adressfenster»: "Fenster und Adressierung sind so abzustimmen, dass keine Angaben sichtbar werden, die nicht zur Adresse gehören. Die Adresse … müssen vollständig lesbar bleiben, auch wenn sich der Adressträger im Umschlag verschiebt." [post.ch](https://www.post.ch/-/media/portal-opp/pm/dokumente/briefe-spezifikation-gestaltung.pdf?sc_lang=de&hash=BB181E74C5D3A0D1D49A954793EA670A)
[^window-ch]: Windows to SN 010130 as Mettler Couvert sells them: C5 65 mm from the bottom, 12 mm from the right ([Art. 8064](https://www.mettler-couvert.ch/C5-Couvert-Fenster-rechts-haftklebend-Ultraweiss-FSC/8064-100-x-45-Fe)) or 20 mm from the left ([Art. 8065](https://www.mettler-couvert.ch/C5-Couvert-Fenster-links-haftklebend-Ultraweiss-FSC/8065)); C5/6 20 mm from the bottom, 12 mm from the right ([Art. 7044](https://www.mettler-couvert.ch/C5-6-Couvert-Fenster-rechts-haftklebend-Recycling-mit-Blauem-Engel/7044)); C4 55 mm from the top, 20 mm from either side ([Art. 9064](https://www.mettler-couvert.ch/C4-Couvert-Fenster-rechts-haftklebend-Ultraweiss-FSC/9064), [Art. 9065](https://www.mettler-couvert.ch/C4-Couvert-Fenster-links-haftklebend-Ultraweiss-FSC/9065)). Swiss Post's ePost prints behind a 100 × 50 mm C5 window and gives 47–81 mm as always visible: [SmartSend Formatierungsvorgaben](https://www.epost.ch/hubfs/25_SmartSend/SmartSend_Formatierungsvorgaben%20PDF/SmartSend%20%E2%80%93%20Formatierungsvorgaben%20(DE).pdf).
[^window-din]: DIN 680:2008-02 *Fensterbriefhüllen – Formate und Fensterstellung*: 20 mm from the left; 15 mm from the bottom on DL and C6/5, 60 mm from the bottom on C5, 57 mm from the top on C4 (Form B; a Form A window sits 17 mm higher and needs `address_top: 45`). The standard is not freely available; the figures are the ones tabulated in [Wikipedia, *Briefumschlag*](https://de.wikipedia.org/wiki/Briefumschlag).
[^s1]: Swico, [*Syntaxdefinition der Rechnungsinformationen (S1) bei der QR-Rechnung*, Version 1.2, 23.11.2018](https://www.swico.ch/media/filer_public/20/76/2076a19a-a017-438a-bc9a-73b1a5980d00/v2_qr-bill-s1-syntax-de.pdf). SIX has taken over the definition and reproduces it in Anhang D of the [Swiss Implementation Guidelines QR-bill 2.4](https://www.six-group.com/dam/download/banking-services/standardization/qr-bill/ig-qr-bill-v2.4-de.pdf).
[^belege]: Art. 957a Abs. 2 Ziff. 2 OR: "der Belegnachweis für die einzelnen Buchungsvorgänge". What a Swiss invoice must state is listed in Art. 26 Abs. 2 MWSTG, and a number is not among it. [fedlex](https://www.fedlex.admin.ch/eli/cc/27/317_321_377/de#art_957_a).
[^sequential]: Art. 226 point (2) of the VAT Directive 2006/112/EC: "a sequential number, based on one or more series, which uniquely identifies the invoice". [EUR-Lex](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:02006L0112-20250414).
