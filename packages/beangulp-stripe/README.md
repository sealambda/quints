# beangulp-stripe

[beangulp](https://github.com/beancount/beangulp) importer for **Stripe
balance transactions**, plus a thin API client. Entity-agnostic: account
names, the fee account, and payee rules are all constructor arguments.

## Input

A JSON file of `/v1/balance_transactions` data — the raw Stripe list
response, a bare array, or a wrapper:

```json
{
  "account": {"id": "acct_..."},
  "balance": {"as_of": "2026-07-10", "available": [...], "pending": [...]},
  "data": [{"object": "balance_transaction", "id": "txn_...", ...}]
}
```

## Behaviour

- `stripe_id:` metadata (the `txn_...` id) is the idempotency key —
  re-imports skip ids already in the ledger.
- Minor units are converted per currency (zero- and three-decimal
  currencies included).
- Cash leg books the **net**; a per-transaction `fee` is split out to the
  fees account so the counter leg is the gross.
- `stripe_fee` transactions (monthly-billed fees debited from the balance)
  go straight to the fees account, review-flagged: the monthly tax invoice
  drives the VAT split at review.
- `payee_rules` `(regex, account, flag)` draft other counter legs; unmatched
  drafts stay `!`-flagged with the cash leg only.
- A `balance` snapshot becomes per-currency `balance` assertions dated the
  day after `as_of`.

## Invoice correlation

`correlate_invoices(transactions, invoices)` pairs each charge balance
transaction with the invoice documenting it, so a caller can file the customer
invoice PDF against the entry it belongs to.

Stripe does not reliably link the two objects — on subscription invoices
`charge`, `payment_intent` and `subscription` can all be `null` — so the
pairing works in two passes:

1. an id named by both objects wins outright (`charge`/`payment_intent`, or
   the newer `payments` list);
2. failing that, same currency, same **gross** amount and created within
   `CREATED_TOLERANCE_SECONDS` of each other — and the agreement has to be
   mutual, one invoice for the transaction and one transaction for the invoice.

A transaction with no candidate is normal and silent: payouts, refunds and
monthly `stripe_fee` debits have no customer invoice. A tie is returned in
`ambiguities` and is expected to stop the caller — filing a PDF against the
wrong entry is worse than filing none. An invoice that names an id but matched
nothing settles a charge outside the window, so it never falls back to the
heuristic.

## Client

```python
from beangulp_stripe import StripeClient

client = StripeClient("rk_live_...")   # restricted key, read-only scopes
txns = client.balance_transactions(created_gte, created_lte)
snapshot = client.balance()
invoices = client.invoices(created_gte, created_lte)
pdf = client.document(invoices[0]["invoice_pdf"])   # bytes
```

Use a **restricted** API key with *Balance transaction sources: Read* and
*Charges: Read* (for `expand[]=data.source` payee names), plus *Invoices:
Read* for `invoices()`. No write scopes.

`invoice_pdf` URLs are signed and short-lived, so fetch them at download time.
`document()` follows the redirects itself, bounded, and sends no
`Authorization` — the key stays on API requests and never travels to the host
a redirect names.
