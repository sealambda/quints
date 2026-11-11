# Getting started

Set up the books for one entity. Hand the job to your AI agent, or do it
yourself in six steps. Both end with a ledger that validates.

!!! abstract "Applies if"
    - **Legal form:** Einzelfirma (sole proprietorship, freelancer), GmbH or
      AG. Partnerships are not supported yet — [Legal forms](legal-forms.md).
    - **VAT status:** any. Not registered yet, registered, and registered
      mid-year all scaffold correctly.
    - **Situation:** new books starting in 2026. For books you already keep
      elsewhere, scaffold empty books (no `--samples`) and book an opening
      balance.

## Hand it to your AI agent

Open Claude Code, Codex or another coding agent in the directory that should
hold the books, and paste:

<!-- agent-prompt -->
```text
Set up my Swiss bookkeeping with quints. Its docs, as Markdown for you:
https://sealambda.github.io/quints/llms.txt

1. Install it: `uv tool install quints` (or `pipx install quints`).
2. Run `quints init --questions --json`. It lists what quints must know
   about my business and where each answer is found.
3. Answer what you already know about my business, from memory or from
   files here. Tell me where each answer came from so I can correct it.
   Ask me the rest in one message. Never guess: the legal form and the VAT
   status decide what I file.
4. Run `quints init <directory>` with every answer as a flag. No `--yes`
   and no `--samples`: these are real books.
5. In the new directory, run `uv sync` and `uv run quints check`. Then read
   `AGENTS.md` there and tell me what I should do first.
```

The agent takes the questions from the installed quints, not from its
training data, so they match your version. It fills in what it already knows
about your business and asks you the rest. Nothing is scaffolded on a guess:
without `--yes`, an unanswered question stops `quints init` before it writes
a file.

Everything the agent reads goes to its model provider. Read
[Your data](reference/ai-agents.md#your-data) before you point it at real
books.

The same setup by hand:

## 1. Install quints

<!-- no-test -->
```bash
uv tool install quints        # or: pipx install quints / pip install quints
```

Python 3.11+ (uv installs it if missing). Nothing else to install.

## 2. Have your answers ready

`quints init` asks for these. The legal form and the VAT situation are facts
you look up, not choices you make here.

| Question | Flag | Where you find the answer |
|---|---|---|
| Legal form | `--legal-form` | The commercial register ([zefix.ch](https://www.zefix.ch)). A freelancer without a company is an `einzelfirma`. [Legal forms](legal-forms.md) |
| Name | `--name` | As registered. For an Einzelfirma without an entry, your own name. |
| VAT-registered since | `--vat-registered-since` | The start date on the ESTV's registration confirmation, or `no` if you are not registered.[^threshold] |
| VAT method | `--vat-method` | `saldo` only if the ESTV approved the Saldosteuersatz method for you; otherwise `effective`.[^sss] |
| Saldosteuersatz | `--saldo-rate` | The rate on that approval, in per cent. Only with `saldo`. |
| Report language | `--lang` | `en` or `de`. A preference; every command overrides it. |

Not sure whether you must register, or which method fits?
[Register, switch method, deregister](guides/vat-registration.md) has the
criteria.

The tool prints the same list, with `--json` for an agent or a script:

```bash
quints init --questions
```

## 3. Scaffold the books

Pass the answers as flags. Leave one out and `quints init` asks for it.

=== "Not VAT-registered"

    Not VAT-registered: no VAT on invoices, nothing to file.

    ```bash
    quints init jane-books \
      --name "Jane Doe" --legal-form einzelfirma \
      --vat-registered-since no \
      --lang en --samples
    ```

=== "Effective method"

    Registered under the effective method: you deduct input VAT and file
    quarterly.

    ```bash
    quints init acme-books \
      --name "Acme GmbH" --legal-form gmbh \
      --vat-registered-since 2026-01-01 --vat-method effective \
      --lang en --samples
    ```

=== "Saldosteuersatz"

    Registered under the Saldosteuersatz method, with a flat rate granted by
    the ESTV (here 6.2 %): you file half-yearly.

    ```bash
    quints init edelweiss-books \
      --name "Edelweiss AG" --legal-form ag \
      --vat-registered-since 2026-01-01 --vat-method saldo --saldo-rate 6.2 \
      --lang en --samples
    ```

Swap `--legal-form` freely: the VAT flags work the same for all three forms.

- **`--samples`** books a demo quarter so every command below has data. Drop
  it for real books.
- **`--importers ubs,yapeal,wise,stripe`** pre-configures statement
  importers.
- **`--yes`** skips the questions and fills in demo answers. Use it to try
  quints, never for real books.

## 4. Look at what you got

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
so every later change, yours or an agent's, is a reviewable diff. Only the
files it wrote are committed: anything already in the directory stays
untracked. `--no-git`
opts out. Scaffolding inside an existing repository never creates a nested
one.

## 5. Validate, and run the first reports

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

## 6. Make it yours

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

[^threshold]: Art. 10 Abs. 2 Bst. a MWSTG: a business with less than CHF 100'000 turnover a year is exempt from VAT liability. [SR 641.20](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_10).
[^sss]: Art. 37 MWSTG, [SR 641.20](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_37).
