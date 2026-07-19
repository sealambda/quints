# Working with AI agents

quints is built to be driven by an AI coding agent — Claude Code, Codex, the
Agent SDK. The division of labour is strict:

- **The agent** reads documents, proposes bookings, extends the chart of
  accounts, writes transactions. The judgement calls.
- **quints** validates, computes, reports. Deterministically. It never calls
  a model, and the same input always produces the same output.

Numbers you file come from quints, not from the model. An agent that invents
a VAT figure is a bug; the playbook tells it to compute instead.

## AGENTS.md

`quints init` scaffolds an `AGENTS.md` into every project: the layout, the
KMU-code rule, both halves of the loop (bank statements in, invoices out),
the configured importer roster with its credentials, and the
machine-readable surfaces. A one-line `CLAUDE.md` (`@AGENTS.md`) sits next
to it, so a Claude Code session loads the playbook automatically — other
agents find `AGENTS.md` by convention. The fenced commands in the generated
file run in CI against the example project, like every page of these docs.

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
quints mwst -q 2026-Q3 --json
quints status --json
quints report bilanz --at 2026-12-31 --json
quints receivables --json
```

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
