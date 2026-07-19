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
   - `quints import stripe --fetch --from <date> --to <date>` — needs `QUINTS_STRIPE_API_KEY` in `.env` (a restricted read-only key for the `[import.stripe]` account).
2. Review each draft in `staging/`. A draft is a flagged (`!`) transaction
   with only the cash leg known:

```beancount
2026-07-20 ! "ACME AG" "Payment order"
  Assets:CH:GmbH:Current:UBS:CHF  -250.00 CHF
```

   Complete the counter leg, decide the VAT treatment (InputVAT /
   Bezugsteuer / none), link the source document, flip `!` to `*`, and move
   it into `books/2026.bean`. `quints match` scores staging drafts and
   inbox documents against invoices and bookings.
3. **Always** `quints check` before you consider the books consistent.

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

## Machine-readable surfaces (prefer these over scraping text)

Every reporting command takes `--json` — stable keys, ISO dates, decimal
strings:

```bash
quints check --json
quints mwst -q 2026-Q3 --json
quints status --json
quints report bilanz --at 2026-12-31 --json
quints receivables --json
```

JSON Schemas for the invoicing files are hosted at
https://sealambda.github.io/quints/schema/ (`quints schema` writes them
locally to `invoicing/schema/`).

Never invent VAT numbers or rates — compute them with `quints mwst`.

## Sample data — replace before the books are real

The scaffold seeded a demo quarter so every command has data. Before
booking real activity:

- [ ] `invoicing/issuer.yaml` — the VAT ID (CHE-267.359.056 MWST) and both
      IBANs are checksum-valid fakes; put the real ones in.
- [ ] `invoicing/customers.yaml` — replace the demo customers (acme, globex).
- [ ] `invoicing/acme-2026-07.yaml` and `invoicing/globex-2026-08.yaml`
      — delete the demo invoices.
- [ ] `books/2026.bean` — delete the block marked *sample activity*.
- [ ] `prices.bean` — drop the demo EUR rates, then `quints prices sync`.
- [ ] `quints.toml` — the placeholder IBAN under `[import.ubs]`.
