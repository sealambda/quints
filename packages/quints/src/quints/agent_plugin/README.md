# quints skills for AI agents

Skills for an AI agent that keeps Swiss books with
[quints](https://github.com/sealambda/quints):

- **book-expense:** a receipt or supplier invoice, booked with the VAT legs
  the entity's VAT status calls for, and its document filed.
- **reconcile:** bank and payment-provider statements imported, every
  draft worked into the books, the balance assertions landed.
- **close-vat-period:** a VAT quarter, half-year or year checked for
  completeness, reported, settled, then tracked until it is filed and paid.

The skills say how a job is done. The books' own `AGENTS.md`, written by
`quints init`, says who the company is: legal form, VAT status, importers.

## A human approves every booking

quints keeps books for a company, so the agent never has the last word:

- Bank imports land as drafts in `staging/`. A draft the agent can't
  explain stays there until a person explains it.
- quints computes every VAT figure and FX rate. The agent never invents
  one.
- `quints check` validates the books, and every change is a git diff a
  person reviews before it is committed.
- quints never files or pays anything. The return goes into the ESTV
  ePortal, and the payment leaves the bank, by a person's hand.

quints itself runs locally and calls no model. Your agent sends what it
reads to its model provider: read
[Your data](https://sealambda.github.io/quints/reference/ai-agents/#your-data)
before you point it at real books.

## Install

The skills need the `quints` command:

```bash
uv tool install quints
```

In Claude Code:

```text
/plugin install quints --marketplace sealambda/quints
```

Books scaffolded by `quints init` already carry the skills, in
`.claude/skills/` and `.agents/skills/`, so Codex, Cursor, Copilot,
opencode and the other agents that read those directories find them too.
After upgrading quints, refresh them from inside the books:

```bash
quints skills install
```

## Try it

The repository's example project is a demo GmbH with a booked quarter
and an unimported UBS statement:

```bash
git clone https://github.com/sealambda/quints
cd quints/packages/quints/examples
```

Open your agent there and ask:

1. "Reconcile `statements/ubs-2026.mt940`. Book what you can, and list
   what you could not, with the question you need answered."
2. "On 2026-08-01 we paid EUR 100 for a software subscription from a
   German company. The invoice shows no VAT. Draft the booking and show
   me the VAT legs before you write anything."
3. "Close 2026-Q3: check nothing is missing, show me the return, and
   settle it."

The example's company, customers and bank accounts are invented. Never
file anything from them.
