---
name: reconcile
description: Reconcile bank and payment-provider statements against quints books - run the configured importers, work the staging/ drafts down to zero (counter leg, VAT decision, document), and land the statement's balance assertions. Use when the user says "reconcile" or "import the bank statement", hands over a statement file, or before a VAT period or year is closed.
---

# Reconcile statements into the books

Input: fresh statement files, or a date range for the importers that
fetch. Output: every draft booked or raised with the user, the
statement's balance assertions in the books, and `quints check` green.
Never book a guess: a draft you can't explain stays in `staging/`.

Not professional tax advice: this skill and quints work out figures from
what is booked. They don't judge the user's tax position. Say so when you
show the user a VAT decision or a return, and leave a question of judgement
to them or their Treuhänder.

## 1. Import

`AGENTS.md` lists the importers configured in `quints.toml` and what each
needs. For example:

```bash
quints import ubs <statement.mt940>
quints import wise --fetch --from <date> --to <date>
```

Imports are idempotent: each draft carries its source id, so a statement
imported twice books nothing twice. A draft that matches an existing
booking by amount within three days is reported, not drafted. Check each
of those matches: a wrong one hides a real transaction.

Each run writes a new file under `staging/`.

## 2. Work the drafts to zero

Oldest first:

- **`*` drafts** (a customer payment matched to its invoice, a payment of a
  supplier bill, the ESTV): check the counter account and the amount
  against the open item, then move the draft into `books/<year>.bean` in
  date order.
- **`!` drafts** know the cash leg only. Complete them as the
  `book-expense` skill does: counter account, the VAT legs for the
  entity's VAT status, the linked document. Chase the user for a missing
  document.
- A short payment, an overpayment or a payment without a reference is
  drafted `!` with a `;` comment saying how it was allocated. Confirm it
  with the user before it moves.
- Anything you can't explain stays in `staging/`. List it for the user.

To pair drafts and `inbox/` documents with invoices, bookings and open
supplier bills, run:

```bash
quints match
```

## 3. Assertions and check

Move the statement's closing `balance` assertions from the staging file
into `books/<year>.bean`, then:

```bash
quints check --review
quints receivables
quints payables
```

The check must pass. It checks the ledger, the statements and the VAT
return of every period not settled yet; each problem names its
`file:line` and a code, and `quints explain <code>` says how to fix it,
with no network. `--review` adds what is still to review: a `!` flag left
in the books, an entry with no document, a bank account whose balance
wasn't asserted for a quarter. The other two show what is still open.

Show the user what the bookings do, and the list of what stayed in
`staging/`:

```sh
quints diff --base-ref HEAD --output-format markdown
```

It lists each transaction added, whether it links a document, and how the
result, total assets and the current VAT period move: a review in money,
next to `git diff`. Commit only once the user has approved the bookings,
then delete the emptied staging file.

If every month leaves the same kind of residue in `staging/`, the
importer's `rules` in `quints.toml` want a new entry: suggest it to the
user.
