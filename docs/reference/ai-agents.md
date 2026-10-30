# Working with AI agents

quints is built to be driven by an AI coding agent — Claude Code, Codex, the
Agent SDK. The division of labour is strict:

- **The agent** reads documents, proposes bookings, extends the chart of
  accounts, writes transactions. The judgement calls.
- **quints** validates, computes, reports. Deterministically. It never calls
  a model, and the same input always produces the same output.

Numbers you file come from quints, not from the model. An agent that invents
a VAT figure is a bug; the playbook tells it to compute instead.

The same goes for rules. Every page of these docs opens with an *Applies if*
block (legal form, VAT status, method), gives the steps, and footnotes each
legal claim to its source: the article, the ESTV publication, the standard.
An agent can check that a rule applies to the entity in front of it, and
show a human where it came from.

## Your data

quints itself sends nothing anywhere: it runs locally and calls no model. The
agent is different. Everything it reads goes to its model provider: bookings,
`customers.yaml`, the PDFs under `documents/`, bank statements. That includes
personal data: an Einzelfirma customer's name and address, an employee's
salary.

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

`quints init` scaffolds an `AGENTS.md` into every project: the layout, the
KMU-code rule, both halves of the loop (bank statements in, invoices out),
the configured importer roster with its credentials, and the
machine-readable surfaces. A one-line `CLAUDE.md` (`@AGENTS.md`) sits next
to it, so a Claude Code session loads the playbook automatically — other
agents find `AGENTS.md` by convention. The fenced commands in the generated
file run in CI against the example project, like every page of these docs.

It opens with the entity's **VAT status**: whether the books are
registered, under which method, how often they file, and that every change
to that goes into `quints.toml` before the period it affects. An agent that
books a purchase needs to know this first, because it decides whether there
is a VAT leg at all.

The statement loop the playbook describes:

1. `quints import …` drafts statements into `staging/`.
2. The agent reviews each draft — VAT decision, linked document — and moves
   it to `books/<year>.bean`.
3. `quints check` before the books are considered consistent.

The scaffold is also a git repository with the pristine project as its
initial commit — "what did the agent change" is always one `git diff` away,
and a wrong edit is a revert, not an archaeology dig. Always.

## Machine-readable surfaces

Every reporting command takes `--json`:

```bash
quints vat liability --at 2026-12-31 --json
quints vat report -p 2026-Q3 --json
quints vat status --json
quints report bilanz --at 2026-12-31 --json
quints receivables --json
quints payables --json
quints invoice invoicing/acme-2026-07.yaml --json
```

The invoice one adds `reference` and `reference_type` (the payment reference
it printed), `customer_reference`, and the ledger draft when the invoice is
not booked yet.

Stable keys, ISO dates, decimal strings — made to be parsed, not scraped.

```bash
quints schema
```

writes JSON Schemas for the invoice/issuer/customers files, so agents (and
editors) validate invoice YAML before rendering. The same schemas are
published with this site under `/quints/schema/`, and scaffolded YAMLs point
their `$schema` modelines there — validation works before anyone runs the
command.

## Determinism as a guarantee

Scaffolding (`quints init`) is deterministic too: the same answers produce
byte-identical projects. The repo's example project is regenerated from an
answer file and byte-checked in CI — the same mechanism that runs every
command in these docs against a real ledger. What you read here is executed,
not asserted.

[^processor]: Art. 9 DSG (processing by a processor), [SR 235.1](https://www.fedlex.admin.ch/eli/cc/2022/491/de#art_9). For personal data of people in the EU, also Art. 28 GDPR.
[^abroad]: Art. 16 f. DSG (disclosure abroad: a country with adequate protection per Annex 1 DSV, or safeguards such as standard contractual clauses), [SR 235.1](https://www.fedlex.admin.ch/eli/cc/2022/491/de#art_16); Art. 44 ff. GDPR.
[^inform]: Art. 19 Abs. 4 DSG: the information duty names the country a disclosure abroad goes to. [SR 235.1](https://www.fedlex.admin.ch/eli/cc/2022/491/de#art_19).
