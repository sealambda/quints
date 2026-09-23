# Getting started

Set up the books for one entity. Three facts decide what quints scaffolds:
the legal form, whether you are VAT-registered, and which VAT method you use.
Have them ready. The pages linked at each step help if you're unsure.

!!! abstract "Applies if"
    - **Legal form:** Einzelfirma (sole proprietorship, freelancer), GmbH or
      AG. Partnerships are not supported yet — [Legal forms](legal-forms.md).
    - **VAT status:** any. Not registered yet, registered, and registered
      mid-year all scaffold correctly.
    - **Situation:** new books starting in 2026. For books you already keep
      elsewhere, scaffold empty books (no `--samples`) and book an opening
      balance.

## 1. Pick the legal form and the VAT situation

The legal form decides the account namespace and the equity block
([Legal forms](legal-forms.md)). The VAT situation decides:

- whether invoices carry VAT
- which accounts the books use
- how often you file

[Register, switch method, deregister](guides/vat-registration.md) explains
all three.

=== "Not VAT-registered"

    Below the CHF 100'000 threshold. No VAT on invoices, nothing to file.

    ```bash
    quints init jane-books --name "Jane Doe" --legal-form einzelfirma --vat-registered-since no --lang en --samples --yes
    ```

=== "Effective method"

    Registered; you deduct input VAT and file quarterly.

    ```bash
    quints init acme-books --name "Acme GmbH" --legal-form gmbh --vat-registered-since 2026-01-01 --vat-method effective --lang en --samples --yes
    ```

=== "Saldosteuersatz"

    Registered with a flat rate granted by the ESTV (here 6.2 %); you file
    half-yearly.

    ```bash
    quints init edelweiss-books --name "Edelweiss AG" --legal-form ag --vat-registered-since 2026-01-01 --vat-method saldo --saldo-rate 6.2 --lang en --samples --yes
    ```

Swap `--legal-form` freely: the VAT flags work the same for all three forms.

Other options:

- **No flags:** `quints init` asks the same questions interactively.
- **`--samples`:** books a demo quarter so every command has data. Drop it
  for empty books.
- **`--importers ubs,yapeal,wise,stripe`:** pre-configures statement
  importers.
- **`--lang de`:** makes reports German by default.

## 2. Look at what you got

```text
jane-books/
├── main.bean          # options, plugins, includes — the entry point
├── accounts.bean      # chart of accounts, every account with its KMU code
├── commodities.bean   # currencies + their price sources
├── prices.bean        # FX rates (quints prices sync)
├── books/2026.bean    # transactions, one file per fiscal year
├── invoicing/         # issuer + sample invoices (with --samples)
├── quints.toml        # everything entity-specific, the VAT situation included
├── pyproject.toml     # so uv sync makes bean-check and fava work
├── AGENTS.md          # playbook for an AI coding agent
├── CLAUDE.md          # @AGENTS.md — auto-loads the playbook in Claude Code
├── inbox/             # drop source documents here
├── staging/           # importer drafts land here (gitignored)
└── documents/         # filed documents, mirroring the account tree
```

The project is a normal beancount ledger. After `uv sync` in it, the standard
toolchain (`bean-check main.bean`, `fava main.bean`) works, not only
`quints`.

It is also a git repository. `quints init` commits the untouched scaffold,
so every later change, yours or an agent's, is a reviewable diff. `--no-git`
opts out. Scaffolding inside an existing repository never creates a nested
one.

## 3. Validate, and run the first reports

```bash
cd jane-books
quints check
quints vat liability --at 2026-12-31
quints report bilanz --at 2026-12-31
cd ..
```

`quints check` is bean-check plus the KMU guard: every account of the entity
must carry a four-digit `kmu:` code, or the ledger doesn't validate. Run it
before you trust any number.

A registered project files instead of tracking the threshold:

```bash
cd acme-books
quints vat report -p 2026-Q3
cd ..
```

![Scaffolding a project with samples, validating it, and printing the VAT return](assets/quickstart.gif)

## 4. Make it yours

- **`invoicing/issuer.yaml`:** replace the sample identity and bank
  accounts. `AGENTS.md` lists every placeholder.
- **Sample activity:** delete the *sample activity* block in
  `books/2026.bean`.
- **Chart of accounts:** add accounts as `open` directives in
  `accounts.bean`, each with the KMU code it rolls up to:

```beancount
2026-01-01 open Expenses:CH:Einzelfirma:Marketing:Ads CHF
  kmu: "6600"  ; Advertising
```

`quints report konten --year 2026` shows the codes already in use.

If your VAT situation changes later (you register, switch method,
deregister), record it in `quints.toml` rather than scaffolding again:
[Register, switch method, deregister](guides/vat-registration.md).

## Next

- [Import bank statements](guides/importing.md) — the staging review loop.
- [Invoicing](guides/invoicing.md) — QR-bill PDFs, cross-checked against the ledger.
- [File your VAT return](guides/vat.md) — once you're registered.
- [Working with AI agents](reference/ai-agents.md) — hand the loop to an agent.
