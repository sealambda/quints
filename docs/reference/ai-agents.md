# Working with AI agents

quints is built to be driven by an AI coding agent: Claude Code, Codex,
Cursor, GitHub Copilot, opencode, or one built on an agent SDK. The split is
strict:

- **The agent** reads documents, proposes bookings, extends the chart of
  accounts and writes transactions: the judgement calls.
- **quints** validates, computes and reports. It never calls a model, and
  the same input always gives the same output.

Numbers you file come from quints, never from the model. So do the rules:
every page of these docs opens with an *Applies if* block and footnotes each
legal claim to its source, so an agent can check that a rule applies to the
entity in front of it and show a human where it came from.

## Start from zero

[Getting started](../getting-started.md#hand-it-to-your-ai-agent) has a
prompt to paste into the agent. It rests on one command:

```bash
quints init --questions --json
```

It lists every question `quints init` asks: the flag that answers it, the
allowed values, when it applies, and where the answer is found (the
commercial register, the ESTV's registration confirmation). The agent
answers what it knows, asks a human the rest, and passes each answer as a
flag. `quints init --json` echoes the answers it scaffolded from.

`quints init` never prompts an agent. With a question unanswered and no
`--yes`, it exits non-zero before it writes anything. `--yes` fills in demo
answers: for trying quints, never for real books.

## Your data

quints sends nothing anywhere: it runs locally and calls no model. The agent
sends everything it reads to its model provider: bookings, `customers.yaml`,
the PDFs under `documents/`, bank statements. That includes personal data:
an Einzelfirma customer's name and address, an employee's salary.

The provider processes that data on your behalf, often outside
Switzerland.[^processor] Before you point an agent at real books:

- **Check the provider's terms.** You need a data-processing agreement (DPA),
  the countries it processes in, and whether it trains on your data. Prefer a
  plan that excludes training.[^abroad]
- **Say so in your privacy notice** if customers or employees are natural
  persons.[^inform]
- **Keep the git remote private.** `documents/` is committed on purpose, so
  the repository holds every invoice and receipt.
- **Keep credentials out of reach.** `.env` and `.wise/` are git-ignored, and
  the scaffolded `AGENTS.md` tells the agent never to open them.

## AGENTS.md

`quints init` scaffolds an `AGENTS.md`: the layout, the KMU-code rule, the
statement and invoice loops, the configured importers with their
credentials, and the machine-readable surfaces. Codex, Cursor, GitHub
Copilot, opencode, Amp, Goose and most other coding agents read it on their
own.[^agentsmd] Claude Code reads `CLAUDE.md`, so a one-line `CLAUDE.md`
(`@AGENTS.md`) pulls it in. Its fenced commands run in CI against the
example project, like every page of these docs.

It opens with the entity's **VAT status**: registered or not, the method,
the filing period, and that every change to those goes into `quints.toml`
before the period it affects. The status decides whether a booking has a
VAT leg at all.

The statement loop it describes:

1. `quints import …` drafts statements into `staging/`.
2. The agent reviews each draft (VAT decision, linked document) and moves
   it to `books/<year>.bean`.
3. `quints check` passes before the books count as consistent.

After each edit, `quints check --review --strict --base-ref HEAD` reports
only the problems the last commit doesn't have. The scaffolded
`.claude/settings.json` runs it after every edit Claude Code makes, and
`AGENTS.md` tells any other agent to run it.
[Check the books as you edit](../guides/editing.md) has the setup for each
agent and editor.

The scaffold is a git repository with the pristine project as its first
commit, so what the agent changed is one `git diff` away and a wrong edit
is a revert.

## Skills

`AGENTS.md` says who the company is. Skills say how a recurring job is
done, step by step:

- **`book-expense`:** a receipt or supplier invoice, booked with the VAT
  legs the VAT status calls for, and its document filed.
- **`reconcile`:** statements imported, every draft booked or raised with
  you, the balance assertions landed.
- **`close-vat-period`:** a quarter, half-year or year checked for
  completeness, reported and settled, exported for upload to the ESTV
  portal, then tracked until it is filed and paid.

`quints init` writes each skill twice: to `.claude/skills/` for Claude Code
and to `.agents/skills/` for Codex. Most other agents read one of the
two.[^skills] The skills are the same for every company: they read the VAT
status and the account names from `AGENTS.md` and `quints.toml`.

They come with the installed quints. After an upgrade, refresh them from
inside the books:

```bash
quints skills install
```

It overwrites the skill files and nothing else; `git diff` shows what
changed. In Claude Code, the same skills also install as a plugin:

```text
/plugin install quints --marketplace sealambda/quints
```

Every skill keeps a human in the loop. A draft the agent can't explain
stays in `staging/`, quints computes every VAT figure and rate, and a person
files the return and makes the payment.

## Machine-readable surfaces

Every reporting command takes `--json`, with stable keys, ISO dates and
decimal strings:

```bash
quints vat liability --at 2026-12-31 --json
quints vat report -p 2026-Q3 --json
quints vat status --json
quints report bilanz --at 2026-12-31 --json
quints receivables --json
quints payables --json
quints invoice invoicing/invoices/acme-2026-07.yaml --json
quints digest --output-format json
```

The invoice one adds `reference` and `reference_type` (the payment reference
it printed), `customer_reference`, and the ledger draft when the invoice is
not booked yet. `quints diff` takes `--output-format json` too.

Each problem in `quints check --json` carries its `code`, `file` and `line`,
a `help` link to its entry in [Diagnostics](diagnostics.md), and a `fix`
when one needs no judgement: the line to insert and where. This prints the
same entry with no network, for a model that runs offline:

```bash
quints explain vat-mismatch
```

JSON Schemas for the invoice, issuer and customers files and for
`quints.toml` are published with this site under `/quints/schema/`. The
scaffolded files point at them, so an agent or an editor validates a file
before quints reads it. This writes the same schemas locally:

```bash
quints schema
```

## These docs, as Markdown

- [`/llms.txt`](https://sealambda.github.io/quints/llms.txt) indexes a
  Markdown copy of every page.
- [`/llms-full.txt`](https://sealambda.github.io/quints/llms-full.txt) is
  all of them in one file.
- Every page has its copy at `index.md` under its own URL, and a *Copy as
  Markdown* button.

The copies are generated from these pages on every deploy.

## Determinism as a guarantee

`quints init` is deterministic too: the same answers produce a
byte-identical project. CI regenerates the repo's example project from an
answer file and byte-checks it, then runs every command in these docs
against it.

[^agentsmd]: [agents.md](https://agents.md) lists the agents that read the file.
[^skills]: [Agent Skills](https://agentskills.io) lists the agents that load skills, and where each looks for them.
[^processor]: Art. 9 DSG (processing by a processor), [SR 235.1](https://www.fedlex.admin.ch/eli/cc/2022/491/de#art_9). For personal data of people in the EU, also Art. 28 GDPR.
[^abroad]: Art. 16 f. DSG (disclosure abroad: a country with adequate protection per Annex 1 DSV, or safeguards such as standard contractual clauses), [SR 235.1](https://www.fedlex.admin.ch/eli/cc/2022/491/de#art_16); Art. 44 ff. GDPR.
[^inform]: Art. 19 Abs. 4 DSG: the information duty names the country a disclosure abroad goes to. [SR 235.1](https://www.fedlex.admin.ch/eli/cc/2022/491/de#art_19).
