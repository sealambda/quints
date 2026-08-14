# Invoicing

## Render a QR-bill

```bash
quints invoice invoicing/acme-2026-07.yaml
```

Writes a Swiss QR-bill PDF (domestic, or export/reverse-charge without QR
part) and cross-checks the total against your ledger: if the invoice is
already booked at a different amount, you get a conflict, not a silent
divergence. If it isn't booked yet, quints prints the draft transaction to
paste into `books/<year>.bean`.

The PDF is filed the way beancount documents are filed — under the income
account's folder, date-prefixed, so `option "documents"` and Fava pick it up:

```text
documents/Income/CH/GmbH/Consulting/External/Domestic/2026-07-02.acme-ag.INV2026014.pdf
```

`--out` overrides the location when you need to.

![Rendering the two sample invoices — domestic QR-bill and EUR export — each cross-checked against the ledger](../assets/invoice.gif)

![The generated Swiss QR-bill PDF, with receipt and payment part](../assets/invoice-qr-bill.png){ width="480" }

An invoice is one YAML file:

```yaml
number: INV2026014
kind: domestic          # or export (reverse charge, no QR part)
currency: CHF
issue_date: 2026-07-02
supply: Juli 2026
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

## Bank details

One account per invoicing currency, under `bank`:

```yaml
bank:
  CHF:
    qr_iban: CH74 3000 5263 1434 9501 E  # QR-bill with a QRR reference
    iban: CH27 0026 3263 1434 9501 E     # CHF arriving from abroad
    bic: UBSWCHZH80A
    bank_name: UBS Switzerland AG, Zürich # optional
  EUR:
    iban: BE11 9679 6818 4648
    bic: TRWIBEB1XXX
    bank_name: Wise Europe SA, Brussels
    holder: Sealambda GmbH                # optional — if not the issuer
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
quints iban "BE11 9679 6818 4648" --bic TRWIBEB1XXX
quints iban
```

It reports the country, the institution (IID) for Swiss and Liechtenstein
IBANs, and flags a BIC whose country doesn't match the IBAN's — normal for a
payment provider (a Wise EUR account is a Belgian IBAN), wrong if you pasted
another account's code. It exits non-zero when something is off, so it fits a
pre-flight check.

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

## Foreign invoices

```bash
quints invoice invoicing/globex-2026-08.yaml
```

An export invoice (`kind: export`) renders without a QR part — it shows the
full SEPA/international payment instruction instead (beneficiary, IBAN,
BIC/SWIFT, bank, reference) — and defaults to the EU B2B reverse-charge note,
which requires the customer's VAT number in the registry. Set
`reverse_charge: false` for customers outside a reverse-charge regime (e.g.
US). The currency's account needs both a regular `iban` (a QR-IBAN can't
receive an ordinary credit transfer) and a `bic`.

![The generated export invoice PDF — no QR part, SEPA IBAN and reverse-charge note instead](../assets/invoice-export.png){ width="480" }

A project scaffolded with `quints init --samples` includes both flavours:
`invoicing/acme-<year>-07.yaml` (domestic QR-bill) and
`invoicing/globex-<year>-08.yaml` (EUR export), each tied to a booking in the
sample quarter so the cross-check reconciles.

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
the same `^invoice-id` link.

Each invoice shows in its own currency; below the per-currency totals a
consolidated total converts everything at the latest rate in your price file
(`≈ Total CHF`). It consolidates in the operating currency by default —
`--in EUR` picks another. A currency with no rate is excluded and called out;
`quints prices sync` fixes that.

![quints receivables lists open invoices aged by due date](../assets/receivables.gif)
