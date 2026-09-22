# Working on Example GmbH's books with an AI agent

These are plain-text ([beancount](https://beancount.github.io)) books managed
with [`quints`](https://github.com/sealambda/quints). **`quints` is a
deterministic tool — you drive it, it never calls a model.** Your job is to
extend and maintain the ledger; `quints` validates and reports on it.

## Setup

`uv sync` once — it installs quints, which brings beancount and fava along.
Then `uv run quints check` (or activate the venv and call `quints` and the
standard beancount tools directly).

This project is a git repository; `quints init` committed the pristine
scaffold. Work in reviewable steps: `git diff` before moving drafts into
`books/`, commit once `quints check` passes — the history is the audit trail.

## Layout

- `main.bean` — options, plugins, includes; the entry point every tool loads.
  `plugin "quints.plugins.kmu"` is enabled: every `*:CH:GmbH:*` account
  **must** be opened with a four-digit `kmu:` code (Swiss KMU Kontenrahmen).
- `accounts.bean` — the chart of accounts (all `open` directives).
- `books/2026.bean` — transactions, one file per fiscal year. `main.bean`
  includes `books/*.bean`, so a new year just needs a new file.
- `commodities.bean` — currencies; `prices.bean` — FX rates, refresh with
  `quints prices sync`.
- `quints.toml` — entity config (name, legal form, VAT, importer rules). VAT
  *rates* are law and live in code, not here.
- `staging/` — importer drafts land here (git-ignored, transient).
- `inbox/` — incoming source documents, not yet filed.
- `documents/` — filed documents, mirroring the account tree as
  `documents/<Account/Tree>/YYYY-MM-DD.payee.description.pdf`. Committed:
  the ledger links to these files (`fava.plugins.link_documents`).
- `invoicing/` — issuer identity (`issuer.yaml`), customer registry
  (`customers.yaml`), one YAML per issued invoice.

## Extending the chart of accounts (the part that needs judgement)

Add income/expense sub-trees for this business as `open` directives in
`accounts.bean`, each with the KMU code it rolls up to, e.g.:

```beancount
2026-01-01 open Expenses:CH:GmbH:Marketing:Ads CHF
  kmu: "6600"  ; Advertising
```

See the codes already in use:

```bash
quints report konten --year 2026
```

Pick codes from the KMU Kontenrahmen; `quints check` fails on a `:CH:GmbH:`
account with no valid `kmu:` code.

## The loop — money out (statements → books)

1. Draft bank/PSP activity into `staging/`. Configured importers:
   - `quints import ubs <statement.mt940>` — the MT940 export from UBS e-banking; no credentials.
   - `quints import wise --fetch --from <date> --to <date>` — needs `QUINTS_WISE_API_TOKEN` in `.env` (plus `QUINTS_WISE_PRIVATE_KEY` for SCA-protected profiles; the key pair lives in `.wise/`, git-ignored).
   - `quints import stripe --fetch --from <date> --to <date>` — needs `QUINTS_STRIPE_API_KEY` in `.env` (a restricted read-only key for the `[import.stripe]` account). Add `--invoices` to file each charge's customer invoice PDF into `inbox/` (needs *Invoices: Read* on the key).
2. Review each draft in `staging/`. A draft is a flagged (`!`) transaction
   with only the cash leg known:

```beancount
2026-07-20 ! "ACME AG" "Payment order"
  Assets:CH:GmbH:Current:UBS:CHF  -250.00 CHF
```

   Complete the counter leg, decide the VAT treatment (InputVAT /
   Bezugsteuer / none), link the source document, flip `!` to `*`, and move
   it into `books/2026.bean`. `quints match` scores staging drafts and
   inbox documents against invoices, bookings and open supplier bills.

3. A **supplier bill** is booked when it arrives, not when it is paid:
   against `Liabilities:CH:GmbH:Payable:Trade`, with the supplier's own
   invoice number in `bill:` and the payment term in `due:`.

```beancount
2026-08-20 * "Treuhand Muster" "Bookkeeping" ^BILL-4711
  bill: "BILL-4711"
  due: 2026-09-19
  Expenses:CH:GmbH:Admin:Bookkeeping       480.00 CHF
  Liabilities:CH:GmbH:Payable:Trade       -480.00 CHF
```

   The bank draft that pays it clears the liability — matched to the bill,
   its counter leg becomes that clearing. `quints payables` shows what is
   still owed, aged by due date.
4. **Always** `quints check` before you consider the books consistent.

## The loop — money in (invoice → receivable → payment)

1. Describe the invoice as a YAML file in `invoicing/` (each file carries a
   `$schema` modeline, so schema-aware editors validate it as you type).
2. `quints invoice invoicing/<file>.yaml` renders the PDF into `documents/`
   under the income account and cross-checks the total against the ledger.
   Not booked yet? It prints the receivable draft to paste into
   `books/2026.bean`.
3. The payment arrives with the next bank import; the draft is matched to
   the open invoice by its QR/SCOR reference. `quints receivables` shows
   what is still open.

Export invoices carry a full SEPA/SWIFT instruction and will not render
without a `bic` under the currency's account in `invoicing/issuer.yaml` —
never invent one, ask the account holder's bank. `quints iban` checks the
IBAN/BIC pairs already configured (`quints iban <IBAN> --bic <BIC>` checks a
new one) and exits non-zero if anything is off.

## The loop — year-end (closing 2026)

Run `quints prices sync` first (it needs network), then work the checklist:

```bash
quints close check --year 2026
quints fx revalue --at 2026-12-31
quints close depreciation --year 2026
```

`close check` lists what still stands between these books and a closed year —
VAT periods settled and paid, no `!` drafts, a bank balance assertion dated
1 January 2027 or later, FX revaluation and depreciation booked — and
names the command that fixes each one. It reports and exits 0; `--strict`
makes a failing item exit 1, which is the flag to gate on. The other two
print entries to review and paste into `books/2026.bean`; run them again
afterwards and the delta is zero. Finish with
`quints report statements --year 2026` for the Treuhänder's PDF.

Depreciation is driven by metadata on the fixed-asset `open` directives in
`accounts.bean` (`depreciation:`, `depreciation_rate:`, `residual:`), never by
a number you invent — the maxima are the ESTV Merkblatt A/1995 Normalsätze.

## Machine-readable surfaces (prefer these over scraping text)

Every reporting command takes `--json` — stable keys, ISO dates, decimal
strings:

```bash
quints check --json
quints vat report -q 2026-Q3 --json
quints vat status --json
quints close check --year 2026 --json
quints report bilanz --at 2026-12-31 --json
quints receivables --json
quints payables --json
```

JSON Schemas for the invoicing files are hosted at
https://sealambda.github.io/quints/schema/ (`quints schema` writes them
locally to `invoicing/schema/`).

Never invent VAT numbers or rates — compute them with `quints vat report`.

## Sample data — replace before the books are real

The scaffold seeded a demo quarter so every command has data. Before
booking real activity:

- [ ] `invoicing/issuer.yaml` — the identity is Sealambda's (name, VAT ID,
      logo) and the bank accounts are the standard documentation IBANs,
      valid but nobody's. Replace all of it with your own — invoices ask
      to be paid into whatever is in this file — and swap or delete
      `invoicing/wordmark.svg`.
- [ ] `invoicing/customers.yaml` — replace the demo customers (acme, globex).
- [ ] `invoicing/acme-2026-07.yaml` and `invoicing/globex-2026-08.yaml`
      — delete the demo invoices.
- [ ] `books/2026.bean` — delete the block marked *sample activity*.
- [ ] `prices.bean` — drop the demo EUR rates, then `quints prices sync`.
- [ ] `quints.toml` — the placeholder IBAN under `[import.ubs]`.
