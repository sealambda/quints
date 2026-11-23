---
name: book-expense
description: Book a receipt or supplier invoice (usually a PDF in inbox/) into quints books - read it, pick the expense account, decide the VAT legs for the entity's VAT status, file the document, draft the transaction and validate it with quints check. Use when the user hands over an expense document, says "book this receipt/invoice", or asks to record a purchase.
---

# Book an expense

Input: a receipt or invoice, usually under `inbox/`, and who paid it with
what. Output: one transaction in `books/<year>.bean` with its document
filed under `documents/`. Never book a transaction without its document,
and never book a guess: ask the user what the document doesn't say.

Not professional tax advice: this skill and quints work out figures from
what is booked. They don't judge the user's tax position. Say so when you
show the user a VAT decision or a return, and leave a question of judgement
to them or their Treuhänder.

## 1. Read the VAT status first

`AGENTS.md` (section *VAT status*) and `[entity]` in `quints.toml` say
whether the books are VAT-registered and under which method. A
`[[vat.change]]` entry means the method on the **invoice date** applies.
The account names used below are under `[accounts]` in `quints.toml`.

## 2. Read the document

Capture the payee (the legal entity in the footer, not the brand), the
invoice date, the currency, net, VAT and gross amounts, what was bought,
and any Swiss VAT number (`CHE-xxx.xxx.xxx MWST`) or reverse-charge note
("VAT 0%", "reverse charge", the buyer's own VAT number).

## 3. Pick the expense account

Choose from the chart in `accounts.bean`; `quints report konten` lists
the KMU codes in use. If nothing fits, open a new account with the KMU
code it rolls up to (`AGENTS.md`, *Extending the chart of accounts*)
rather than force-fitting an existing one.

## 4. Decide the VAT legs

**Not VAT-registered:** book gross, no VAT legs at all. One exception to
watch: services bought from abroad owe Bezugsteuer once they pass
CHF 10'000 in a calendar year, and the business must then register
(Art. 45 MWSTG). quints doesn't track that threshold, so tell the user
when such purchases add up.

**Saldosteuersatz method:** book purchases gross, never InputVAT. A
service bought from abroad still owes Bezugsteuer, as a cost:
`quints vat convert <net> <CCY> <date> --bezugsteuer` prints the pair.

**Effective method:**

- **Swiss VAT on the invoice** (a CHE number and a VAT line): expense leg
  = net, VAT to `input_vat`.
  - In CHF: post the invoice's VAT figure.
  - In a foreign currency, and the invoice states the VAT in CHF: book that
    CHF figure, annotated `@@ <vat> <CCY>`. Don't recompute it.
  - In a foreign currency without a CHF VAT figure:
    `quints vat convert <vat> <CCY> <date>` prints the CHF posting at the
    rate in `prices.bean`.
- **A service from a foreign supplier, no Swiss VAT:** Bezugsteuer. The
  expense leg is the full net invoice, and
  `quints vat convert <net> <CCY> <date> --bezugsteuer` prints the
  InputVAT and Bezugsteuer pair.
- **Neither** (exempt, out of scope): no VAT legs, and a `;` comment on the
  transaction saying why.

Add `--rate reduced` or `--rate lodging` for those rate classes. Never
compute a VAT figure or a rate yourself.

A refund is a negative expense on the same account, with VAT legs that
mirror the original.

## 5. The payment leg

- **Paid from a company account:** the bank or Wise account it left from,
  with `@@` for a cross-currency payment. The statement import later finds
  it already booked.
- **Not paid yet:** against `payable`, with the supplier's invoice number
  in `bill:`, the term in `due:`, and a `^<bill>` link. The bank draft that
  pays it clears the liability (`AGENTS.md`, *The loop — money out*).
- **Paid personally by an owner:** ask whether it is to be reimbursed (a
  loan from the owner) or a contribution, and to which account.

## 6. File the document

Move it to
`documents/<Account/Tree>/YYYY-MM-DD.payee.description.pdf`: the folder is
the expense account with `:` replaced by `/`, the date is the invoice
date. Create the folder if needed. Link it from the transaction with
`document: "<file name>"`.

## 7. Validate

Insert the transaction in date order in `books/<year>.bean` and run:

```bash
quints check
```

It is the gate. Beyond the balances and the document links, it checks
that the account's KMU code lands in a statement row, that every foreign
amount has a rate, and the VAT return of every period not settled yet: a
VAT leg that doesn't match net × rate fails here. Each problem names its
`file:line` and a code. `quints check --json` lists them under `errors`
and `warnings` (the flag also keeps the output the same where
`QUINTS_OUTPUT_FORMAT` is set), and this says what a code means and how
to fix it, with no network:

```bash
quints explain vat-mismatch
```

Fix every error before you go on. On books that already have open
problems, this reports only the ones your booking brought, and also what
it leaves to review (a `!` flag, an entry with no document):

```sh
quints check --review --strict --base-ref HEAD
```

Books scaffolded by `quints init` run it in Claude Code after every edit
(`.claude/settings.json`).

Show the user the entry, with the VAT decision and the reason for it, and
what it does to the figures:

```sh
quints diff --base-ref HEAD --output-format markdown
```

It lists the transactions added, whether each links a document, and how
the result, total assets and the current VAT period move. Commit the
transaction and the document together, and only once the user has
approved them.
