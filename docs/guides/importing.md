# Import statements

Importers never write to your books. Drafts land in `staging/` (gitignored);
you review, complete, move to `books/<year>.bean`, delete the draft. Nothing
reaches the ledger unreviewed.

## UBS (MT940)

```bash
quints import ubs statements/ubs-2026.mt940
```

Drafts every movement on the account into `staging/`. Each draft is flagged:

- `*` — complete as drafted (a rule matched and the booking is unambiguous)
- `!` — needs your VAT decision and a linked document before it moves to books

Rules live in `quints.toml` under `[import.ubs]` — `[payee regex,
counter account, flag]` triples. See [quints.toml](../reference/configuration.md).

## Wise and Stripe

<!-- no-test: --fetch needs API credentials and network -->
```bash
quints import wise --fetch --from 2026-07-01 --to 2026-09-30
quints import stripe --fetch --from 2026-07-01 --to 2026-09-30
```

`--fetch` pulls from the APIs: `QUINTS_WISE_API_TOKEN` (plus
`QUINTS_WISE_PRIVATE_KEY` for SCA-protected profiles) and
`QUINTS_STRIPE_API_KEY` in `.env`. Both commands also accept already-fetched
JSON files as arguments. Wise conversions are merged into one transaction;
Stripe fees and payouts are split out, with the VAT inside Stripe's own fees
drafted against InputVAT.

Imports are idempotent — each transaction carries its source id
(`mt940_ref:`, `wise_id:`, `stripe_id:`), so re-importing a statement never
duplicates a booking.

## Payments that quote an invoice

An incoming payment is matched to an open invoice by the reference it carries
— the QR reference or RF creditor reference from the QR-bill, or the plain
invoice number — read out of the payee, narration and metadata, verified by its
check digits, and matched **exactly**, never by substring. A match turns the
draft's counter leg into the receivable clearing, adds `invoice:` metadata and
the `^number` link, and flags it `*`. A reference that fits more than one open
invoice matches nothing: the draft stays `!` and `quints match` says why. How
the references are built, and which one your QR-bills use, is in
[Payment references](payment-references.md).

### Stripe customer invoices

<!-- no-test: needs API credentials and network -->
```bash
quints import stripe --fetch --from 2026-07-01 --to 2026-09-30 --invoices
```

`--invoices` additionally files the **invoice** PDF Stripe renders for each
charge (`invoice_pdf`) into `inbox/`, named
`YYYY-MM-DD.customer.INVOICENUMBER.pdf` — the same convention `quints invoice`
files its own PDFs under. That is the document carrying the customer's name,
address and tax number, which is what substantiates the place of supply behind
an export booking; the payment itself is already evidenced by the `stripe_id:`
reference on the draft.

The key needs *Invoices: Read* on top of the fetch scopes. Every file is
reported on its own line, and anything already in `inbox/` or `documents/` is
left alone, so re-runs download nothing.

Stripe doesn't reliably link an invoice to the balance transaction that pays
it, so the two are correlated on amount, currency and timing when no shared id
exists. If a charge could belong to more than one invoice, the command says so
and files nothing rather than guessing — the drafts are already in `staging/`
by then, so only the documents are affected.

Stripe's own monthly fee invoices are a separate matter: they have **no API**.
When a month's fee debit carries VAT, the import says which month needs its
tax invoice pulled by hand from the Dashboard (Settings → Plans and fees →
Invoice history), which the quarterly VAT close needs.

## Review helpers

```bash
quints match
quints inbox
```

`match` scores staging drafts and inbox documents against invoices and
existing bookings, so you see what belongs together before you book. `inbox`
inventories `inbox/` — filename hints, duplicates, documents already linked.

Drop source PDFs into `inbox/` named `YYYY-MM-DD.payee.narrative.pdf`; once
booked, file them under `documents/` mirroring the account path, and link
them with `document:` metadata on the transaction.
