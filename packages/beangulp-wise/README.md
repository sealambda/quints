# beangulp-wise

[beangulp](https://github.com/beancount/beangulp) importer for **Wise**
(formerly TransferWise) balance statements, plus a thin, SCA-capable API
client to fetch them.

Wise has no official Python SDK, and the community clients are either
experimental or AGPL-licensed; the import workflow needs exactly three GET
endpoints, so this package owns a ~100-line client instead
(`beangulp_wise.client.WiseClient`).

## Design

The importer consumes **`statement.json` files** (one per currency balance),
not the API directly — fetching writes files to disk first, so every import
is auditable, replayable, and testable offline.

- `referenceNumber` (`CARD-…`, `TRANSFER-…`, `CONVERSION-…`) is stored as
  `wise_id:` metadata and is the idempotency key: `extract()` skips references
  already anywhere in the existing ledger. **Re-imports are always safe.**
- Payee by detail type: merchant name for card payments, recipient/sender for
  transfers, description otherwise.
- `totalFees` becomes an explicit posting to `fees_account` — fees stay
  visible instead of dissolving into the counter leg.
- `payee_rules` (`(regex, account, flag)`) draft the counter leg; unmatched
  entries keep the `!` review flag and the cash leg only.
- `endOfStatementBalance` → a `balance` assertion the day after the interval.
- **Conversions**: each leg appears in a different currency's statement,
  sharing one `referenceNumber`. `merge_conversions(entries)` joins them into
  a single two-currency transaction — the incoming leg priced `@@` what left
  the other balance net of fees, so it balances exactly.

## Usage

```python
from beangulp_wise import Importer, merge_conversions

importer = Importer(
    {"EUR": "Assets:Wise:EUR", "USD": "Assets:Wise:USD"},
    fees_account="Expenses:BankFees:Wise",
    holder="My GmbH",                       # a token may see several profiles
    payee_rules=((r"cloudflare", "Expenses:IT:Hosting", "!"),),
)
entries = merge_conversions(
    importer.extract("statement-eur.json", existing=ledger_entries)
    + importer.extract("statement-usd.json", existing=ledger_entries)
)
```

## Fetching statements (SCA)

Statement endpoints are protected by Strong Customer Authentication: Wise
answers 403 with a one-time token in the `x-2fa-approval` header, which must
be signed (RSA-SHA256, PKCS#1 v1.5) with a private key whose **public half is
registered on the Wise account** (Settings → API tokens → Manage public keys).

```python
from beangulp_wise import WiseClient

client = WiseClient(token, private_key_pem=open("private.pem", "rb").read())
pid = client.profile_id("My GmbH")
for balance in client.balances(pid):
    statement = client.balance_statement(
        pid, balance["id"], balance["currency"],
        "2026-07-01T00:00:00.000Z", "2026-07-31T23:59:59.999Z",
    )
```

`cryptography` is an optional dependency (`beangulp-wise[sca]`) — importing
statement files works without it.
