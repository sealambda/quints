# Diagnostics: what each problem means

`quints check` names every problem it finds with a code, a severity and,
when there is one, the file and line it is about. This page lists every
code: what it means, and how to fix it. The SARIF output links each code
here.

!!! abstract "Applies if"
    - **Legal form:** Einzelfirma, GmbH or AG. Nothing on this page differs
      between them.
    - **VAT status:** any. The `vat-…` codes appear only for a period you
      were registered for and have not settled yet. Not registered, you see
      none of them.
    - **VAT method:** effective or Saldosteuersatz. Two codes appear only
      under the Saldosteuersatz: `vat-saldo-input` and
      `vat-output-no-turnover`.
    - **Filing period:** quarter, half-year or year. The period only decides
      which VAT returns `check` runs: every one not settled yet.

## How to read a diagnostic

```bash
quints check
```

Each problem prints as `path:line: message [code]`, with the path relative
to the repository root, or as `path:line: warning: message [code]`. A
problem with no single place in the books prints as
`quints: message [code]`.

- An **error** fails `quints check` (exit 1): a statement or a VAT return
  would be wrong.
- A **warning** is a judgement call. It exits 0, unless you pass `--strict`.

The reports (`quints report …`, `quints vat report`) list the same problems
under their figures, and in `--json` under `violations`, each with its
`code`, `severity`, `file` and `line`.

`check` runs the VAT returns of the periods not settled yet. A settled
period is corrected by a counter-entry in a later one, so its old problems
don't fail `check`. `quints vat report` for that period still lists them.

## ledger

Error. Beancount could not load part of the books: a syntax error, an
`include` that matches no file, a transaction that doesn't balance, a
posting to an account that was never opened, a failing `balance` assertion,
or an entity account without a `kmu:` code. What beancount can't read is
left out of every figure. Fix the entry at the line given. The text output
shows the entry under the message.

## rate-missing

Error. An amount in a foreign currency has no CHF rate on its date, so it is
left out of the statements or the VAT return. Run `quints prices sync`, or
book the amount with its rate (`@` or `@@`). Only accounts of the entity
need a rate: those that contain `entity_marker` in `quints.toml`.

## kmu-unmapped

Error. The account's `kmu:` code lands in no row of the statement that holds
it: Income and Expenses in the Erfolgsrechnung, the other accounts in the
Bilanz. Its amounts are left out. Use a code of the KMU Kontenrahmen that
the statement has a row for, on the `open` directive the diagnostic points
at.

## statements-mismatch

Error. The Bilanz result differs from the Erfolgsrechnung result plus the
unrealised FX. Double entry makes them equal for every account both
statements see, so one statement misses something: usually a
`kmu-unmapped` or `rate-missing` reported next to it. Fix those first.
`check` compares the two statements from 1 January to the latest
transaction.

## vat-leg-currency

Warning. A VAT leg (output VAT, input VAT or Bezugsteuer) is booked in a
foreign currency. The return counts it at its CHF value, but a foreign
balance left on a VAT account never clears with the settlement. Book VAT
legs in CHF: `quints vat convert` gives the amount.

## vat-token-unknown

Error. A `mwst:` tag holds a word quints doesn't know, so the posting may
land under the wrong Ziffer. Use one of the tags in
[How quints fills the VAT return](vat-rules.md).

## vat-saldo-input

Error. Input VAT is booked under the Saldosteuersatz method. The flat rate
already compensates the input VAT, and the form has no input VAT block.
Book purchases gross, without an input VAT leg.

## vat-input-credit

Error. Input VAT is credited without a credit note on the same
transaction. Tag it `mwst: "vorsteuerkorrektur"` (Ziffer 415) or
`mwst: "vorsteuerkuerzung"` (Ziffer 420). Input VAT booked before the
liability began is reversed on the day it starts.

## vat-turnover-untagged

Error. Income outside codes 3000–3899 carries no `mwst:` tag. It may be
Entgelt (exempt rent, a side business) or not (damages, a released
provision), and Ziffer 200 must count every Entgelt. Tag it `exempt`,
`taxable`, `donation`, `subvention` or `not_entgelt`.

## vat-output-no-turnover

Error. Under the Saldosteuersatz method, output VAT is booked without
turnover on the same transaction. The form taxes the gross Entgelt, so book
the sale to an income account.

## vat-mismatch

Error. The output VAT a sale posts differs from its net × the rate by more
than a few Rappen. Common causes: the old rate applied by habit, a missing
VAT leg, VAT on an exempt supply. Fix the booking; the message names the
rate quints expected.

## vat-turnover-total

Error. Ziffer 299 differs from the total of the turnover rows: turnover
landed in no row of the return. Look for income on an account the VAT
markers in `quints.toml` don't classify.

## due-unreadable

Warning. A `due:` on a bill or a VAT settlement isn't a date, so it is
listed without one. Write `due: YYYY-MM-DD`.

## receivable-untracked

Warning. The receivables account holds an amount with no invoice id: an
opening balance or a payment without `invoice:` or a `^link`. The open list
then disagrees with the account balance. Link the posting to its invoice.

## invoice-hash

Error. A filed invoice PDF no longer matches the `sha256:` booked with it:
someone changed an issued invoice. Restore the file from git, or reissue it
(`quints invoice --reissue`) and book the new hash.

## invoice-duplicate

Error. Two transactions book the same invoice number. Each issued invoice
has one number, booked once.

## invoice-order

Error. An invoice is dated before the invoice preceding it in its series.
Check the number or the date.

## invoice-unfiled

Warning. A booking carries the hash of an invoice PDF that is not under
`documents/`. File the PDF there.

## invoice-unhashed

Warning. A booking names its invoice PDF without a `sha256:`. Run
`quints invoice` on the invoice file: it prints the line to add.

## invoice-gap

Warning. Numbers are missing between two invoices of a series. Swiss law
doesn't ask for gapless numbers, but an auditor asks about the gap.

## What quints doesn't do here

- **No inline suppression.** A problem in an open period can't be marked as
  accepted: fix it, or settle the period if the return is filed. A settled
  period's problems no longer fail `check`.
- **No severity overrides.** The severity of each code is fixed. `--strict`
  is the one switch: warnings fail too.
