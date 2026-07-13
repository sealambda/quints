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

## Client

```python
from beangulp_stripe import StripeClient

client = StripeClient("rk_live_...")   # restricted key, read-only scopes
txns = client.balance_transactions(created_gte, created_lte)
snapshot = client.balance()
```

Use a **restricted** API key with *Balance transaction sources: Read* and
*Charges: Read* (for `expand[]=data.source` payee names). No write scopes.
