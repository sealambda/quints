# Getting started

## Scaffold your books

Pick your legal form. It decides the account namespace and the equity block —
see [Legal forms](legal-forms.md) for what changes and why.

=== "Freelancer (Einzelfirma)"

    ```bash
    quints init jane-books --name "Jane Doe" --legal-form einzelfirma --lang en --samples
    ```

=== "GmbH"

    ```bash
    quints init acme-books --name "Acme GmbH" --legal-form gmbh --lang en --samples
    ```

=== "AG"

    ```bash
    quints init edelweiss-books --name "Edelweiss AG" --legal-form ag --lang en --samples
    ```

Run `quints init` with no flags to answer the same questions interactively.
`--samples` books a demo quarter so every command works immediately; drop it
for empty books. `--importers ubs,wise,stripe` pre-configures statement
importers. `--lang de` makes reports German by default.

## What you get

```text
jane-books/
├── main.bean          # options, plugins, includes — the entry point
├── accounts.bean      # chart of accounts, every account with its KMU code
├── commodities.bean   # currencies + their price sources
├── prices.bean        # FX rates (quints prices sync)
├── books/2026.bean    # transactions, one file per fiscal year
├── invoicing/         # issuer + sample invoices (with --samples)
├── quints.toml        # everything entity-specific
├── pyproject.toml     # so uv sync makes bean-check and fava work
├── AGENTS.md          # playbook for an AI coding agent
├── inbox/             # drop source documents here
├── staging/           # importer drafts land here (gitignored)
└── documents/         # filed documents, mirroring the account tree
```

The generated project is a normal beancount ledger. After `uv sync` in it,
the standard toolchain works — `bean-check main.bean`, `fava main.bean` —
not only `quints`.

## First commands

```bash
cd jane-books
quints check
quints mwst -q 2026-Q3
quints report bilanz --at 2026-12-31
```

`quints check` is bean-check plus the KMU guard: every entity account must
carry a four-digit `kmu:` code, or the ledger doesn't validate. Run it before
you trust any number.

## Extend the chart

Add accounts as `open` directives in `accounts.bean`, each with the KMU code
it rolls up to:

```beancount
2026-01-01 open Expenses:CH:Einzelfirma:Marketing:Ads CHF
  kmu: "6600"  ; Advertising
```

`quints report konten` shows the codes already in use; `quints check` rejects
an entity account without a valid code.

## Next

- [Import bank statements](guides/importing.md) — the staging review loop.
- [Quarterly VAT](guides/vat.md) — file your MWST return.
- [Invoicing](guides/invoicing.md) — QR-bill PDFs, cross-checked against the ledger.
