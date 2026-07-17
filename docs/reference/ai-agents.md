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
KMU-code rule, the review loop, and the machine-readable surfaces. Point your
agent at the project and it knows how to work. The loop it follows:

1. `quints import …` drafts statements into `staging/`.
2. The agent reviews each draft — VAT decision, linked document — and moves
   it to `books/<year>.bean`.
3. `quints check` before the books are considered consistent. Always.

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
editors) validate invoice YAML before rendering.

## Determinism as a guarantee

Scaffolding (`quints init`) is deterministic too: the same answers produce
byte-identical projects. The repo's example project is regenerated from an
answer file and byte-checked in CI — the same mechanism that runs every
command in these docs against a real ledger. What you read here is executed,
not asserted.
