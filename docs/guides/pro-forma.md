# Get paid before you invoice

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

Open them in `accounts.bean` (a project scaffolded by `quints init` already
has them):

```beancount
2026-01-01 open Liabilities:CH:GmbH:Advances:Domestic
  kmu: "2030"  ; Advances received
2026-01-01 open Liabilities:CH:GmbH:Advances:Export
  kmu: "2030"  ; Advances received
```

## 1. Render the pro forma

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

<!-- no-test: the pro forma flow runs end to end in tests/test_proforma.py -->
```bash
quints invoice invoicing/globex-pf2026001.yaml
```

The PDF is titled *Proforma-Rechnung* / *Pro forma invoice*, never
*Rechnung*. It carries a note that it is a request for an advance, which the
invoice will credit together with the VAT it contains. It asks to be paid,
with a QR-bill or the SEPA block and a reference derived from `PF2026001`.
Swiss law treats any document that bills a supply as an invoice, whatever its
title,[^rechnung] and VAT shown on an invoice is owed as shown.[^art27] The
advance's VAT is owed anyway, in the period the money arrives.[^advance-vat]
The invoice that follows names that VAT again on the advance row, so it isn't
shown twice (see step 3). The pro forma carries no Swico billing information, because
the payer's accounts-payable software would book that as the invoice. It is
filed under the advances account:

```text
documents/Liabilities/CH/GmbH/Advances/Export/2026-11-02.globex.PF2026001.pdf
```

Nothing is booked yet. quints prints the legs the payment will need.

## 2. Book the payment when it arrives

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

## 3. Invoice the supply and credit the advance

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

The PDF shows the invoice total with VAT on the whole supply, then each
advance with the VAT it contained (*Anzahlung PF2026001 vom 20.11.2026, inkl.
MWST 810.00* on a domestic invoice), then the **amount due**. The pro forma
already showed that VAT. Naming it on the advance row shows that it is credited
here, not charged a second time. An export invoice shows no VAT, so its advance
rows show none either. The QR-bill asks for the amount due only. An invoice the
advances cover in full asks for nothing and carries no payment part. The draft
books the whole income, releases the advance as its receipt booked it, and
leaves the rest as receivable:

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

## 4. File the two returns

<!-- no-test: needs the bookings above -->
```bash
quints vat report -p 2026-Q4
quints vat report -p 2027-Q1
```

Q4 2026 declares the advance, Q1 2027 the rest. The advance is settled by the
work, not in money, so it is not revalued at year end: `quints fx revalue`
skips it, and the balance sheet carries it at the CHF it was received at
until the invoice releases it.

## What quints doesn't do here

- **Match a pro forma's payment on import.** It isn't booked until paid, so
  `quints import` has no open item to match it to and leaves the draft `!`.
  Complete it with the legs `quints invoice` printed for the pro forma.

[^rechnung]: Art. 3 Bst. k MWSTG: "Rechnung: jedes Dokument, mit dem gegenüber einer Drittperson über das Entgelt für eine Leistung abgerechnet wird, gleichgültig, wie dieses Dokument im Geschäftsverkehr bezeichnet wird". [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_3)
[^art27]: Art. 27 Abs. 1 and 2 MWSTG: "Wer nicht im Register der steuerpflichtigen Personen eingetragen ist …, darf in Rechnungen nicht auf die Steuer hinweisen"; who shows it anyway "schuldet die ausgewiesene Steuer". [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_27).
[^advance-vat]: Art. 40 Abs. 1 Bst. c MWSTG: the tax arises "mit der Vereinnahmung des Entgelts bei Vorauszahlungen für nicht von der Steuer befreite Leistungen" (in force since 1 Jan. 2025). [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_40). MWST-Info 16 *Buchführung und Rechnungsstellung*, Ziff. 1.6.4: "Anzahlungen von Kunden müssen beim Erhalt als Umsatz versteuert werden." [ESTV](https://www.gate.estv.admin.ch/mwst-webpublikationen/public/pages/taxInfos/tableOfContent.xhtml?publicationId=1002536)
[^advance-221]: MWST-Info 15 *Abrechnung und Steuerentrichtung*, Ziff. 1.3: under agreed consideration, Ziffer 200 includes "Sämtliche Zahlungseingänge, die nicht in Rechnung gestellt wurden", and Ziffer 221 "Zahlungseingänge aus erbrachten oder zukünftigen Leistungen, die nicht in Rechnung gestellt wurden und deren Ort der Leistungserbringung im Ausland liegt". [ESTV](https://www.gate.estv.admin.ch/mwst-webpublikationen/public/pages/taxInfos/tableOfContent.xhtml?publicationId=1013189)
[^advance-rate]: Art. 45 Abs. 1 MWSTV: "Zur Berechnung der geschuldeten Mehrwertsteuer sind Entgelte in ausländischer Währung im Zeitpunkt der Entstehung der Steuerforderung in Landeswährung umzurechnen." For an advance that is the day it is received (Art. 40 Abs. 1 Bst. c MWSTG). [fedlex](https://www.fedlex.admin.ch/eli/cc/2009/828/de#art_45)
