# Import statements

Importers never write to your books. The loop:

1. **Import:** drafts land in `staging/` (gitignored).
2. **Review and complete** each draft.
3. **Move** it to `books/<year>.bean`.
4. **Delete** the draft.

!!! abstract "Applies if"
    - **Legal form:** Einzelfirma, GmbH or AG. Same steps for all three.
    - **Sources:** UBS (MT940), Yapeal (CSV), Wise and Stripe (API or JSON
      export).
    - **VAT status:** any. It only changes how a draft is completed —
      [below](#complete-a-draft).

## UBS (MT940)

```bash
quints import ubs statements/ubs-2026.mt940
```

Every movement on the account becomes a draft in `staging/`, flagged:

- `*` — complete as drafted: a rule matched and the booking is unambiguous.
- `!` — needs your VAT decision and a linked document.

Rules are `[payee regex, counter account, flag]` triples under `[import.ubs]`
in [quints.toml](../reference/configuration.md).

## Yapeal (CSV)

`quints import yapeal <statement.csv>` does the same for a Yapeal CSV export,
with rules under `[import.yapeal]`. Enable it with
`quints init --importers ubs,yapeal`, or add the section to `quints.toml`.
The sample is `statements/yapeal.csv`.

## Wise and Stripe

<!-- no-test: --fetch needs API credentials and network -->
```bash
quints import wise --fetch --from 2026-07-01 --to 2026-09-30
quints import stripe --fetch --from 2026-07-01 --to 2026-09-30
```

`--fetch` pulls from the APIs, with `QUINTS_WISE_API_TOKEN` (plus
`QUINTS_WISE_PRIVATE_KEY` for SCA-protected profiles) and
`QUINTS_STRIPE_API_KEY` from `.env`. Without it, pass fetched JSON files as
arguments. Wise conversions are merged into one transaction. Stripe fees and
payouts are split out, and the VAT in Stripe's own fees is drafted against
InputVAT.

## Importing twice

- **Each import writes a new staging file**, never over an earlier one: a
  second import the same day goes to `<date>-<source>-2.bean`. Drafts
  already in an earlier staging file are reported. Move each to `books/`
  once.
- **A booked transaction is never drafted again.** It carries its source id
  (`ubs_ref:`, `bank_ref:`, `wise_id:`, `stripe_id:`). That only covers what
  the ledger loads: with loader errors, the import lists them and warns
  that a draft may repeat an entry the ledger dropped. Run `quints check`
  and fix them first.

## Complete a draft

A `!` draft has the cash leg only. Add the counter leg, link the source
document (`document:`), flip `!` to `*`. The VAT legs depend on your status
on the transaction date:

=== "Not VAT-registered"

    Not VAT-registered, you book gross: no InputVAT, no OutputVAT. A foreign
    service is a plain expense, until such purchases pass CHF 10'000 in a
    calendar year: then you must register for Bezugsteuer.[^bezug]

=== "Effective method"

    Under the effective method, split a purchase with Swiss VAT into net +
    InputVAT, and book a sale's VAT to OutputVAT. For a foreign service,
    `quints vat convert … --bezugsteuer` prints the Bezugsteuer pair in CHF
    at the right rate.

=== "Saldosteuersatz"

    Under the Saldosteuersatz method, book purchases gross: the rate already
    covers input tax, so no InputVAT, ever. Sales still carry the statutory
    VAT to OutputVAT. A foreign service still owes Bezugsteuer, as a cost:
    `quints vat convert … --bezugsteuer` prints the pair.

`quints vat report` flags a VAT leg that doesn't fit the method in force on
that date.

## Payments that quote an invoice

An incoming payment is matched to an open invoice by its reference: the QR
reference or RF creditor reference from the QR-bill, or the plain invoice
number. quints reads it from the payee, narration and metadata, verifies the
check digits, and matches **exactly**, never by substring
([Payment references](payment-references.md)).

- **A match** turns the counter leg into the receivable clearing, with
  `invoice:` metadata and the `^number` link. It is `*` only when the
  payment equals what is open on the invoice.
- **A short payment, an overpayment, or no reference** from a customer with
  open invoices is drafted `!`, with a `;` comment saying how it was
  allocated ([Allocate customer payments](receivables.md)).
- **A reference that fits more than one open invoice** matches nothing. The
  draft stays `!` and `quints match` says why.

### Stripe customer invoices

<!-- no-test: needs API credentials and network -->
```bash
quints import stripe --fetch --from 2026-07-01 --to 2026-09-30 --invoices
```

`--invoices` also files the invoice PDF Stripe renders for each charge
(`invoice_pdf`) into `inbox/`, as `YYYY-MM-DD.customer.INVOICENUMBER.pdf`,
the name `quints invoice` uses. Its customer name, address and tax number
substantiate the place of supply behind an export booking.

- The key needs *Invoices: Read* on top of the fetch scopes.
- Files already in `inbox/` or `documents/` are left alone, so a second run
  downloads nothing.
- Without a shared id, Stripe's invoice and the balance transaction that
  pays it are correlated on amount, currency and timing. If a charge could
  belong to more than one invoice, the command says so and files nothing.
  The drafts are unaffected.

Stripe's own monthly fee invoices have **no API**. When a month's fee debit
carries VAT, the import names the month whose tax invoice you download by
hand from the Dashboard (Settings → Plans and fees → Invoice history) before
you file the VAT return.

## Supplier bills (payables)

Book a supplier bill when it arrives, not when it is paid: the expense (and
any input VAT) against the trade-payables account from `quints.toml`.

```beancount
2026-08-20 * "Treuhand Muster" "Bookkeeping — first half" ^TM-2026-4711
  bill: "TM-2026-4711"
  due: 2026-09-19
  Expenses:CH:GmbH:Admin:Bookkeeping   480.00 CHF
  Liabilities:CH:GmbH:Payable:Trade   -480.00 CHF
```

Both keys are optional:

- **`bill:`** is the supplier's own invoice number. Without it, a lone
  `^link` names the bill. With neither, the bill is grouped by payee and
  amount, and only a payment to the same payee for the same amount clears
  it.
- **`due:`** is what the aging is measured against. Without it, the payment
  terms run from the bill date: 30 days, or `[payables] default_terms_days`.
  A `due:` that isn't a date (`due: end of month`) is listed as a problem,
  and the bill shows without a due date.

The bank payment clears the liability. `quints match` scores every outgoing
draft against the open bills: by the supplier's reference, else by an amount
that fits exactly one open bill of a matching payee, inside the window after
the bill date. An amount that fits two open bills identifies neither: both
are listed, and the draft stays `!`.

## Review helpers

```bash
quints match
quints inbox
quints payables
```

- **`match`** scores staging drafts and inbox documents against invoices,
  open supplier bills and existing bookings.
- **`inbox`** inventories `inbox/`: filename hints, duplicates, documents
  already linked.
- **`payables`** ages the supplier bills you still owe, in their original
  currency plus a consolidated total. `--at` reports as of a date. Settled
  but unpaid VAT sits on `PayableVAT`, not on a supplier bill: it is a footer
  line here, with the detail in `quints vat status`.

All three take `--json`.

Drop source PDFs into `inbox/` named `YYYY-MM-DD.payee.narrative.pdf`. Once
booked, file them under `documents/` mirroring the account path, and link
them with `document:` metadata on the transaction.

## What quints doesn't do here

- **Book a draft for you.** The counter leg, the VAT decision and the
  document are the review step. An AI agent can do it; quints then checks
  the result ([Working with AI agents](../reference/ai-agents.md)).
- **Fetch Stripe's own fee invoices.** Stripe has no API for them. The
  import says which month to download by hand.
- **OCR a PDF.** Documents are matched by filename and metadata, not by
  content.

[^bezug]: Art. 45 Abs. 2 Bst. b MWSTG, [SR 641.20](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_45).
