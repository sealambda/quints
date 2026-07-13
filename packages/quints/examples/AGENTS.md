# Working on Example GmbH's books with an AI agent

These are plain-text ([beancount](https://beancount.github.io)) books managed
with [`quints`](https://github.com/sealambda/quints). **`quints` is a
deterministic tool — you drive it, it never calls a model.** Your job is to
extend and maintain the ledger; `quints` validates and reports on it.

## Layout

- `main.bean` — the ledger (accounts + transactions). `plugin
  "quints.plugins.kmu"` is enabled: every `*:CH:GmbH:*` account **must** be
  opened with a four-digit `kmu:` code (Swiss KMU Kontenrahmen).
- `prices.bean` — FX rates; refresh with `quints prices sync`.
- `quints.toml` — entity config (name, VAT, importer rules). VAT *rates* are
  law and live in code, not here.
- `staging/` — importer drafts land here; `inbox/` — source documents.

## Extending the chart of accounts (the part that needs judgement)

Add income/expense sub-trees for this business as `open` directives, each with
the KMU code it rolls up to, e.g.:

```beancount
2026-01-01 open Expenses:CH:GmbH:Marketing:Ads CHF
  kmu: "6600"  ; Advertising
```

Run `quints report konten` to see the codes already in use. Pick codes from the
KMU Kontenrahmen; `quints check` fails on a `:CH:GmbH:` account with no valid
`kmu:` code.

## The loop

1. Draft bank/PSP activity: `quints import ubs <file>` → `staging/`.
2. Review, add the VAT decision (InputVAT / Bezugsteuer / none) and a linked
   document, then move drafts into `main.bean`.
3. **Always** `quints check` before you consider the books consistent.

## Machine-readable surfaces (prefer these over scraping text)

- Every reporting command takes `--json`: `quints mwst -q 2026-Q3 --json`,
  `quints status --json`, `quints report bilanz --json`.
- `quints schema` writes JSON Schemas for the invoice/issuer/customer files.

Never invent VAT numbers or rates — compute them with `quints mwst`.
