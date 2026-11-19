---
name: close-vat-period
description: Close a Swiss VAT period in quints books - refresh FX rates, check the period is complete, review the VAT report, book the settlement, then track filing and payment with the ESTV. Use when the user says "close Q3", "VAT return", "MWST-Abrechnung", or a quarter, half-year or year has ended.
---

# Close a VAT period

Input: a period, e.g. `2026-Q3`, `2026-H2` or `2026`. Each step below is
reviewed and committed on its own.

## 0. Is there a VAT period to close?

`AGENTS.md` (section *VAT status*) and `quints vat status --json` say how
the entity files today: the method and the period length. Under the
Saldosteuersatz method the period is a half-year.

Not VAT-registered? There is nothing to file. Run
`quints vat liability --at <period end>` instead: it says whether the
turnover now requires registration. Tell the user what it found, and stop.

## 1. Fresh rates

`quints prices sync` (needs the network). If `prices.bean` changed,
commit it on its own: it holds published rates, not bookings.

## 2. Complete the period

Reconcile every statement up to the period end first (the `reconcile`
skill). `staging/` must hold no draft dated in the period.

Then sweep the period's purchases. Every foreign-currency expense must
carry either an InputVAT leg, a Bezugsteuer pair, or a `;` comment saying
why it has neither. Book what is missing as the `book-expense` skill does.
Corrections go into this open period, never into a period already filed.

## 3. Review the report

```bash
quints vat report -p <period>
```

Work the two lists below the Ziffern before you settle:

- **Prüfung:** bookings the form can't represent (a VAT leg that doesn't
  match net × rate, an unknown `mwst:` tag, InputVAT under the
  Saldosteuersatz) and ledger errors. Fix each in the books and run the
  report again.
- **!** notices: the first return after registering, the last before a
  switch of method, the final one. Each names what to book.

Compare the payable amount with the previous period. A large swing is
worth a sentence to the user.

## 4. Settle

```bash
quints vat settle -p <period>
```

It prints the settlement transaction and its balance assertions. Paste
both into `books/<year>.bean` in date order and run `quints check`. Show
the user the return and the entry, and commit only once the user has
approved them: these are the figures they will file.

## 5. File and pay

The user files the return in the ESTV ePortal and pays it. quints does
neither. Both are due 60 days after the period ends.

- Filed: save the filing confirmation under `documents/`, in the folder of
  the `payable_vat` account, and link it from the settlement transaction
  with `document:`.
- Paid: the payment arrives with the next bank import. If an importer
  rule routes the ESTV debit to `payable_vat`, the draft already carries
  the period's `^VAT-<period>` link. Otherwise complete the draft yourself:
  counter leg `payable_vat`, the `^VAT-<period>` link, and suggest the rule
  to the user.

```bash
quints vat status
```

shows each settled period until it is paid, with its due date.
