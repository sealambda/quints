# Allocate customer payments

A customer payment rarely matches one invoice to the cent. It comes in a few
Rappen short, it covers last month's shortfall too, or it arrives as an
instalment with no invoice number on it. This page covers how `quints import`
drafts those payments, how to confirm them, and how to fix an allocation
after it was booked.

!!! abstract "Applies if"
    - **Legal form:** Einzelfirma, GmbH or AG. Same steps for all three.
    - **VAT status and method:** any, until a write-off. Then not
      VAT-registered and VAT-registered differ, see
      [step 5](#5-write-off-what-will-never-be-paid). The effective method and
      the Saldosteuersatz book a write-off the same way.
    - **Invoices:** booked to `Receivable:Trade` with an invoice id
      (`invoice:` or a `^link`), as `quints invoice` drafts them.
    - **Period:** any. Allocation is per payment, not per VAT period.

## 1. Import the statement

```bash
quints import ubs statements/ubs-2026.mt940
```

Each incoming payment from a customer with open invoices is drafted one of
four ways:

| The payment | Draft | Receivable legs |
|---|---|---|
| quotes an invoice and pays exactly what is open | `*` | one, `invoice:` on the transaction |
| quotes an invoice, pays more, and the extra is exactly the customer's other open invoices, oldest first | `!` | one per invoice, each with its own `invoice:` |
| quotes an invoice and pays any other amount | `!` | one, on the quoted invoice |
| quotes no invoice | `!` | one per invoice, oldest due first,[^art87] plus an untagged leg for what is left |

Every `!` draft carries a `;` comment above it saying what was assumed:

```beancount
; overpaid ACME202603 by 0.47 CHF — applied to older open ACME202602 0.47
2026-04-30 ! "Acme AG" "Rechnung ACME202603" ^ACME202602 ^ACME202603
  ubs_ref: "0130120TP1382853"
  Assets:CH:GmbH:Current:UBS:CHF    4075.84 CHF
  Assets:CH:GmbH:Receivable:Trade  -4075.37 CHF
    invoice: "ACME202603"
  Assets:CH:GmbH:Receivable:Trade     -0.47 CHF
    invoice: "ACME202602"
```

A payment that quotes an invoice stays on that invoice: the customer named
the debt.[^art86] quints moves money to another invoice only when the
difference matches that invoice to the cent. It never writes anything off.

## 2. Confirm each `!` draft

Read the comment, then check it against what the customer told you: a
remittance advice, an email, the payment text.

- **The allocation is right:** change `!` to `*`, delete the comment line,
  and move the draft to `books/<year>.bean`.
- **It is wrong:** edit the `invoice:` tags and amounts on the receivable
  legs before you move it. Their sum must still equal the payment.
- **A short payment:** leave it on its invoice. Ask the customer for the
  rest, or write it off in step 5.
- **"left without an invoice":** keep the untagged leg. `quints receivables`
  lists it below the table until you know which invoice it pays.

## 3. Check what is still open

```bash
quints receivables
```

- An invoice with a negative open amount is marked **overpaid**.
- An open invoice and an overpaid one of the same customer that net to zero
  (for example `+0.47` and `-0.47`) get a one-line hint below the table. A
  payment went to the wrong invoice. Fix it in step 4.
- Money on `Receivable:Trade` without an invoice id is listed below the
  table.

The report never moves money between invoices.

## 4. Move a booked payment to another invoice

Don't edit the original booking. Add a zero-sum **relink** entry: two
receivable postings that cancel out, each tagged with the invoice it moves
money to or from.

Here a payment of 4075.84 was booked in full to ACME202603, which was open for
4075.37. The extra 0.47 was ACME202602's shortfall:

```beancount
2026-05-31 * "ACME" "Relink 0.47 from ACME202603 to ACME202602"
  Assets:CH:GmbH:Receivable:Trade   0.47 CHF
    invoice: "ACME202603"
  Assets:CH:GmbH:Receivable:Trade  -0.47 CHF
    invoice: "ACME202602"
```

A posting's `invoice:` overrides the transaction's invoice id. That is what
lets one transaction touch several invoices. The account balance doesn't
change, and both invoices close.

## 5. Write off what will never be paid

Some differences are never paid: a rounding difference, a bank fee the
customer deducted, a discount you agreed to. Book them as a reduction of the
price paid (Entgeltsminderung). The revenue goes down, and so does the output
VAT if you charged any.[^art41] quints doesn't book this for you.

=== "Not VAT-registered"

    Not VAT-registered, the write-off has no VAT leg:

    ```beancount
    2026-06-30 * "ACME" "ACME202602: 0.47 never paid"
      Income:CH:GmbH:Consulting:External:Domestic   0.47 CHF
      Assets:CH:GmbH:Receivable:Trade              -0.47 CHF
        invoice: "ACME202602"
    ```

=== "Effective method"

    Under the effective method, split the gross difference at the invoice's
    rate. At 8.1%, 0.47 holds 0.04 VAT. The `mwst: "reduction"` tag puts the
    net in Ziffer 235 of the return:[^235]

    ```beancount
    2026-06-30 * "ACME" "ACME202602: 0.47 never paid"
      Income:CH:GmbH:Consulting:External:Domestic   0.43 CHF
        mwst: "reduction"
      Liabilities:CH:GmbH:Tax:OutputVAT              0.04 CHF
      Assets:CH:GmbH:Receivable:Trade              -0.47 CHF
        invoice: "ACME202602"
    ```

=== "Saldosteuersatz"

    Sales carry the statutory VAT to OutputVAT under the Saldosteuersatz too,
    so the write-off is booked the same way. The `mwst: "reduction"` tag
    deducts it in Ziffer 235 of the return. There it counts gross (0.47),
    because the Saldosteuersatz applies to turnover including VAT:[^235]

    ```beancount
    2026-06-30 * "ACME" "ACME202602: 0.47 never paid"
      Income:CH:GmbH:Consulting:External:Domestic   0.43 CHF
        mwst: "reduction"
      Liabilities:CH:GmbH:Tax:OutputVAT              0.04 CHF
      Assets:CH:GmbH:Receivable:Trade              -0.47 CHF
        invoice: "ACME202602"
    ```

Use your own legal form's accounts (`Income:CH:Einzelfirma:…` and so on).

## What quints doesn't do here

- **Rank a debt under Betreibung first.** Art. 87 Abs. 1 OR applies an
  unassigned payment to the debt the customer was first prosecuted for
  before it looks at due dates. quints doesn't know about Betreibungen. If
  one is running, edit the draft's `invoice:` tags by hand.
- **Apply a payment when nothing is due yet.** In that case Art. 87 Abs. 3 OR
  picks the debt that is least secured for you. quints still orders by due
  date. Check the draft.
- **Know your due dates.** `quints invoice` doesn't write a `due:` onto the
  invoice booking, so quints orders by invoice date and says so in the
  comment. Add `due: YYYY-MM-DD` to the invoice transaction to order by due
  date instead.
- **Book write-offs, Skonto or bad debts.** Book them by hand as in step 5.
- **Allocate supplier payments by amount.** `quints import` flags a payment
  that quotes a bill but doesn't pay its open amount (`!` with a comment). It
  doesn't split payments across bills. You choose what you pay, so tag the
  payment yourself, with a posting-level `bill:` per bill.

??? info "How it works"
    - **Open amounts** come from `quints receivables`' own computation, at
      the day of the import. One payment in an import uses up what it
      allocates, so a later payment in the same statement can't settle the
      same invoice again.
    - **An exact amount** means within 0.005.
    - **"Older open invoices"** (row 2 of the table) are the other open
      invoices of the same payee and currency, oldest due first. quints adds
      them up one at a time and stops at the first sum that equals the
      extra. If none does, the whole payment stays on the quoted invoice.
    - **A payment with no reference** is matched to a customer the way
      supplier payments are: the payment's payee name contains the invoice's
      payee name, or the other way round. If invoices of more than one payee
      fit, nothing is allocated. A draft that a payee rule already sent to
      another account than `Receivable:Trade` is left as the rule drafted it.
    - **Same-day debts** share a payment that doesn't cover them all in
      proportion to what is open on each.[^art87] The last share absorbs the
      rounding, so the legs always add up to the payment.
    - **What's left** of an unreferenced payment after every open invoice is
      paid stays on an untagged receivable leg. The draft then gets no
      `^link`, because a lone invoice link would claim that leg for its
      invoice.

[^art86]: Art. 86 Abs. 1 OR: the debtor "ist berechtigt, bei der Zahlung zu erklären, welche Schuld er tilgen will." [SR 220, fedlex](https://www.fedlex.admin.ch/eli/cc/27/317_321_377/de#art_86).
[^art87]: Art. 87 OR: without a declaration "ist die Zahlung auf die fällige Schuld anzurechnen, unter mehreren fälligen auf diejenige Schuld, für die der Schuldner zuerst betrieben worden ist, und hat keine Betreibung stattgefunden, auf die früher verfallene" (Abs. 1). "Sind sie gleichzeitig verfallen, so findet eine verhältnismässige Anrechnung statt" (Abs. 2). With none due, the payment goes to the debt "die dem Gläubiger am wenigsten Sicherheit darbietet" (Abs. 3). [SR 220, fedlex](https://www.fedlex.admin.ch/eli/cc/27/317_321_377/de#art_87). quints encodes Abs. 1 and 2 in `quints.importing._by_due_date`.
[^art41]: Art. 41 Abs. 1 MWSTG: "Wird das vom Leistungsempfänger oder der Leistungsempfängerin bezahlte oder mit ihm oder ihr vereinbarte Entgelt korrigiert, so ist im Zeitpunkt, in dem die Korrektur verbucht oder das korrigierte Entgelt vereinnahmt wird, eine Anpassung der Umsatzsteuerschuld vorzunehmen." [SR 641.20, fedlex](https://www.fedlex.admin.ch/eli/cc/2009/615/de#art_41).
[^235]: Ziffer 235 of the VAT return (Entgeltsminderungen). How quints fills it: [VAT rules](../reference/vat-rules.md). The ESTV's sample returns: [effective method](https://www.estv2.admin.ch/mwst/formulare/mwst-form-abr-muster-effektiv-de.pdf), [Saldosteuersatz](https://www.estv2.admin.ch/mwst/formulare/mwst-form-abr-muster-sss-de.pdf).
