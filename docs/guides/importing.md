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
