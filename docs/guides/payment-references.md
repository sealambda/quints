# Payment references

Every invoice quints renders asks to be paid with a **structured reference**, so
the payment arrives carrying its own invoice id. That is what lets
`quints import` and `quints match` credit an incoming payment to the right
invoice instead of guessing from the amount.

Switzerland has two such references, and they are not interchangeable: each one
is tied to a different kind of bank account, and picking the wrong pair produces
a QR-bill banks reject. quints picks the Swiss-native pair whenever your bank
account can carry it, and the ISO one otherwise — and lets you say so
explicitly when you want the other.

## The three reference types

The Swiss QR-bill defines three, in the `Tp` element of the QR code:[^ig]

| Type | Looks like | Account it needs | Currencies |
|---|---|---|---|
| **QRR** — QR reference | `12 34561 00841 17000 00202 60143` | QR-IBAN **only** | **CHF only** |
| **SCOR** — Creditor Reference (ISO 11649) | `RF47 INV2 0260 14` | regular IBAN **only** | CHF and EUR |
| **NON** — no reference | *(empty)* | regular IBAN | CHF and EUR |

[^ig]: [Swiss Implementation Guidelines QR-bill](https://www.six-group.com/en/products-services/banking-services/payment-standardization/standards/qr-bill.html),
    sections 2.10 (QR-IBAN), 2.12.1 (QR reference), 2.12.2 (Creditor
    Reference) and the `RmtInf/Tp`, `RmtInf/Ref` data elements. Version 2.4
    (24 February 2026) is valid from 14 November 2026 and replaces version
    2.3 of 21 November 2025, which stays valid until November 2027. The one
    difference that matters here: from 2.4 a QR-bill in EUR can only be
    IBAN + SCOR or IBAN + unstructured message — the QR scheme is CHF-only.

The pairing is a hard rule of the standard, not a convention:

> Must contain the code QRR where a QR-IBAN is used; where the IBAN is used,
> either the SCOR or NON code can be entered.

and, for the QR reference and the QR-IBAN specifically:

> [The QR reference] can only be used in combination with a QR-IBAN … [and]
> for invoicing in CHF. … The reference must not consist exclusively of zeros.

> [The QR-IBAN] can only be used for invoicing and payments in CHF.

So **QR-IBAN + SCOR**, **IBAN + QRR** and **QR-IBAN in EUR** are all invalid.
quints never emits any of them: it resolves the valid pair from your account,
and refuses an explicit setting that would break the rule rather than quietly
substituting another.

## Side by side

The same invoice under each scheme — the payment part is what the customer
scans or retypes. With a QR-IBAN and the bank's identification, a QR
reference whose tail is the invoice's own digits:

![Payment part paid by QR reference: account CH44 3199 9123 0008 8901 2 (a QR-IBAN), reference 12 34561 00841 17000 00202 60143](../assets/payment-part-qrr.png)

With the regular IBAN, a creditor reference that spells the invoice number out:

![Payment part paid by creditor reference: account CH93 0076 2011 6238 5295 7, reference RF47 INV2 0260 14](../assets/payment-part-scor.png)

## A QR-IBAN is a second account number

This is the part that trips people up. A QR-IBAN is not a formatting variant of
your IBAN — it is a separate number your bank issues you, recognisable by an
institution id in the **30000–31999** range:

```text
IBAN      CH93 0076 2011 6238 5295 7
QR-IBAN   CH44 3199 9123 0008 8901 2
               ^^^^^ 31999 — the QR-IID
```

Both can point at the same underlying account, but they are addressed
separately, and a QR-IBAN cannot receive an ordinary credit transfer at all. An
invoice paid from abroad therefore needs the regular IBAN — which is why an
export invoice in quints always carries SCOR, and why you keep `iban` next to
`qr_iban` even when your QR-bills use the QR reference.

## Which one should you use?

**QRR with a QR-IBAN, if your bank gives you one.** It is the Swiss-native
scheme — the direct successor of the orange ESR/BVR payment slip — and the one
Swiss accounts-payable departments and e-banking apps are built around. Its
decisive property: the payer's bank **refuses any payment to a QR-IBAN that
does not carry a valid QR reference**. The reference cannot be forgotten,
mistyped or left off a manual transfer, so every payment that reaches you is
matchable. Banks also process these payments as reference payments (the ESR
successor product), which is what their reconciliation tooling keys on. quints
uses QRR automatically as soon as the account has a `qr_iban`.

The costs: it is numeric (quints keeps your invoice's digits visible in it —
see below), CHF-only, Switzerland-and-Liechtenstein-only, and it needs two
things from your bank — the QR-IBAN and the six-digit identification that has
to lead every reference you issue.

**SCOR with your regular IBAN, when you have no QR-IBAN or want one readable
reference everywhere.** It works with the account number you already have,
is valid on a QR-bill in CHF *and* EUR, travels in SEPA transfers so a foreign
customer quotes the same reference a domestic one does, and it spells out the
invoice number: `RF47 INV2 0260 14` is readable on a bank statement and
quotable in a reminder email. quints uses it for every account without a
`qr_iban`, for every QR-bill in EUR, and for every export invoice.

Its cost is the mirror image of QRR's strength: on a regular IBAN the
reference is *optional* for the payer. Someone who types the transfer by hand
instead of scanning can leave it out, and that payment arrives with an amount
and a name only — quints then falls back to scoring by payee and amount and
asks you to confirm.

**Prefer SCOR although you have a QR-IBAN?** Set `reference: scor` on the
account. The QR-IBAN stays on file and unused; QR-bills go out with RF
references paid into the regular `iban`.

**NON is not supported by quints.** The standard allows a QR-bill with no
reference at all, but every quints invoice derives its reference from the
invoice number — that is the premise `quints match` is built on. A bill with no
reference would arrive as an amount and a date, which is exactly the
reconciliation problem quints exists to remove.

## What the QR reference contains

A QR reference is 26 digits plus a check digit, and it may not carry letters.
quints encodes the invoice number into it so that a human can still recognise
the invoice on a statement:

```text
INV2026014, bank id 123456 →  12 34561 00841 17000 00202 60143
                              ^^^^^^ ^ ^^^^^^^ ^^^^^^^^^^^^ ^
                              bank id | letters      digits  check digit
                                      form
```

- the first six digits are the identification your bank assigned you
  (`qr_reference_id`; zeros if you have not configured one);
- one digit names the form. `1` is the legible form for the usual shape of
  invoice number — up to four letters, then digits: a block holding the
  letters, then the digits themselves, right-aligned, so the reference ends in
  `…2026014`;
- `2` is the fallback for any other shape (`ACAD202608B`), an opaque
  bijective-base-36 encoding;
- the last digit is the modulo-10 check digit every QR reference carries.

Both forms are injective — `INV0042` and `INV42` never share a reference — and
both decode: `quints import` and `quints match` turn a QR reference from a bank
statement back into the invoice number without needing your bank id. Twelve
letters-and-digits is the most a reference carries; a longer invoice number is
refused when the file loads rather than truncated, because a truncated
reference is a payment credited to somebody else's invoice. The same rule caps
a SCOR reference at 21 characters.

## Configuring it

The account's reference scheme lives in `invoicing/issuer.yaml`, per currency.
An account with only a regular `iban` pays by SCOR — nothing to declare:

```yaml
bank:
  CHF:
    iban: CH93 0076 2011 6238 5295 7
    bic: POFICHBEXXX
    bank_name: PostFinance AG, Bern
  EUR:
    iban: DE89 3704 0044 0532 0130 00
    bic: COBADEFFXXX
```

Add the QR-IBAN and CHF QR-bills switch to QRR, paid into it. Add the six-digit
identification your bank assigns you (UBS calls it the **BESR-ID**) — banks
require it as the first six digits of every reference you issue, and without
it your references go out starting `000000`:

```yaml
bank:
  CHF:
    qr_iban: CH44 3199 9123 0008 8901 2
    qr_reference_id: "123456"   # quoted — YAML reads a bare leading zero as octal
    iban: CH93 0076 2011 6238 5295 7   # keep it: CHF from abroad needs it
    bic: POFICHBEXXX
```

To keep the QR-IBAN on file but issue RF references anyway, say so:

```yaml
bank:
  CHF:
    reference: scor
    qr_iban: CH44 3199 9123 0008 8901 2
    iban: CH93 0076 2011 6238 5295 7
```

`reference: qrr` is accepted too, and means the same as leaving it out on an
account that has a `qr_iban` — except that it is an error on an account that
has none, or on a QR-bill in EUR, instead of a fallback.

## How quints resolves it

| Account has | Invoice | Reference |
|---|---|---|
| `iban` only | domestic | SCOR, into `iban` |
| `qr_iban`, with or without `iban` | domestic, CHF | QRR, into `qr_iban` |
| `qr_iban` and `iban` | domestic, EUR | SCOR, into `iban` — the QR scheme is CHF-only |
| `qr_iban` only | domestic, EUR | **error** — add an `iban` |
| `reference: scor` | domestic | SCOR, into `iban` (error if there is none) |
| `reference: qrr` | domestic, CHF | QRR, into `qr_iban` (error if there is none) |
| `reference: qrr` | domestic, EUR | **error** — CHF only |
| any | export | SCOR (a credit transfer has no QR-bill) |

`quints invoice` prints what it used, so the scheme is visible on every render:

```text
ref QRR 12 34561 00841 17000 00202 60143
```

and `quints invoice … --json` carries it as `reference` and `reference_type`.

## Checking an account before it ships

```bash
quints iban
```

checks every IBAN and BIC in the issuer config — length, national layout,
mod-97 check digits, ISO 9362 for the BIC — and reports the institution behind
Swiss and Liechtenstein IBANs, so you can see at a glance whether a number you
pasted as `qr_iban` really carries a QR-IID (30000–31999). It exits non-zero
when something is off, which makes it a usable pre-flight check.

## Overriding a single invoice

Set `reference:` on the *invoice* only to re-issue one that originally went out
with a reference produced elsewhere:

```yaml
number: INV2026014
reference: RF47INV2026014
```

It is validated by its own check digits when the file loads, and refused if it
doesn't match the scheme the account resolves to — you cannot staple a QR
reference onto an account that pays by SCOR.

## When the payment comes back

`quints import` and `quints match` read the references out of a payment's
details — QR references however the bank groups them, with or without their
leading zeros; RF references with or without spaces; the plain invoice number
as a whole word — verify their check digits, decode a QR reference back into
its invoice number, and match **exactly**, never by substring: a payment for
`ACAD202608B` is never credited to `ACAD202608`. Invoices issued before this
scheme, with the old zero-padded reference, still clear. A reference that fits
more than one open invoice matches nothing; the draft stays flagged `!` and
`quints match` says why. See [Import statements](importing.md).
