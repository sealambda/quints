"""The payment reference an invoice asks to be paid with — one module.

Two schemes exist, and a Swiss QR-bill accepts exactly one of them per
account:

- **SCOR** (Structured Creditor Reference, ISO 11649) — ``RF`` + two mod-97-10
  check digits + up to 21 alphanumeric characters. It carries the invoice
  number itself (``RF47 INV2 0260 14``), works with a *regular* IBAN on a
  QR-bill and travels in SEPA credit transfers. This is the readable default.
- **QRR** (QR reference) — 26 numeric characters plus a mod-10 recursive check
  digit, printed ``2 + 5×5``. It is usable *only* with a QR-IBAN and cannot
  carry letters, so an alphanumeric invoice number has to be encoded. Banks
  assign the issuer a six-digit identification that must occupy the first six
  digits (UBS calls it the BESR-ID); it is configured as
  ``BankAccount.qr_reference_id``.

The QRR body is therefore ``<6-digit bank id><20-digit encoded number>``. The
encoding is bijective base 36 over the upper-cased alphanumeric invoice
number: each character contributes its base-36 value **plus one**, so unlike
plain base-36 it never loses a leading ``0`` and different numbers can never
produce the same reference (``INV01`` ≠ ``INV1``). Twelve characters fit in 20
digits; a longer number is refused instead of silently truncated, because a
truncated reference is a payment credited to the wrong invoice.
``decode_qrr`` inverts it without knowing the issuer's six-digit id, which is
what lets the matcher recognise a reference it never generated.

Both schemes carry only the number's ASCII letters and digits, upper-cased:
two numbers that differ in case or punctuation alone (``INV-014`` / ``INV014``)
share a reference. The matcher reports such a pair as ambiguous rather than
guessing, but a numbering scheme should not rely on it.

``legacy_qrr`` is the scheme quints emitted before this module existed — the
invoice number's digits, zero-padded. It is kept for one reason: invoices
already in customers' hands were printed with it, and the matcher still has
to recognise their payments. It is *not* injective (``ACAD202608`` and
``ACAD202608B`` collapse onto the same reference), which is exactly why it
was replaced.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from stdnum import iso11649
from stdnum.ch import esr
from stdnum.exceptions import ValidationError

if TYPE_CHECKING:
    from .model import BankAccount, Invoice

Scheme = Literal["scor", "qrr"]
"""What an account asks for; `None` on the account means "resolve it"."""

Kind = Literal["QRR", "SCOR"]
"""The reference scheme actually used, spelled the way the QR payload spells it."""

_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
QRR_LENGTH = 27  # 26 body + 1 check digit
QRR_ID_DIGITS = 6  # the bank-assigned identification, first
_QRR_NUMBER_DIGITS = 20  # what is left for the encoded invoice number
QRR_MAX_CHARS = 12  # alphanumeric characters that fit in _QRR_NUMBER_DIGITS
SCOR_MAX_CHARS = 21  # ISO 11649: RF + 2 check digits + 21


@dataclass(frozen=True)
class PaymentReference:
    """One reference, ready for both the QR payload and the printed page."""

    kind: Kind
    value: str  # compact, as it goes into the payload
    formatted: str  # grouped the way the scheme is printed


def yaml_digits(value: object, length: int, field: str) -> str:
    """A digit string that may have arrived as a YAML integer.

    `qr_reference_id: 123456` reaches us as an int, and at its full length
    nothing was lost. `012345` is another story: YAML 1.1 reads a leading
    zero as octal, so the value here would be 5349 and the real id is gone.
    An integer short of the full length is therefore refused with the one fix
    that works — quotes — instead of being zero-filled into a wrong id."""
    if isinstance(value, bool) or not isinstance(value, int):
        return str(value)
    digits = str(value)
    if len(digits) == length:
        return digits
    raise ValueError(
        f"{field} {value!r} has {len(digits)} digits, not {length} — an unquoted "
        f"number with a leading zero is read by YAML as octal and mangled before "
        f'quints sees it. Quote it: {field}: "0…"'
    )


def compact_number(number: str) -> str:
    """The invoice number as a reference carries it: upper-case, alphanumeric."""
    return re.sub(r"[^0-9A-Z]", "", number.upper())


# ── SCOR (ISO 11649) ──────────────────────────────────────────────────────────


def make_scor(number: str) -> str:
    """The Creditor Reference for `number` (check digits per ISO 7064 mod 97-10)."""
    ref = compact_number(number)
    if not ref:
        raise ValueError(
            f"invoice number {number!r} has no alphanumeric character, so no "
            f"payment reference can be derived from it"
        )
    if len(ref) > SCOR_MAX_CHARS:
        raise ValueError(
            f"invoice number {number!r} is {len(ref)} alphanumeric characters; a "
            f"SCOR (ISO 11649) creditor reference carries at most {SCOR_MAX_CHARS}. "
            f"Shorten the number — quints refuses to truncate it, because a "
            f"truncated reference is a payment credited to another invoice"
        )
    check = 98 - int("".join(str(int(c, 36)) for c in ref + "RF00")) % 97
    return f"RF{check:02d}{ref}"


# ── QRR (Swiss QR reference) ──────────────────────────────────────────────────


def _encode(number: str) -> int:
    """Bijective base 36 — injective over strings, leading zeros included."""
    value = 0
    for ch in number:
        value = value * 36 + _ALPHABET.index(ch) + 1
    return value


def _decode(value: int) -> str:
    out: list[str] = []
    while value > 0:
        value -= 1
        out.append(_ALPHABET[value % 36])
        value //= 36
    return "".join(reversed(out))


def check_qr_reference_id(value: str) -> str:
    """The bank-assigned six-digit identification, as configured."""
    digits = value.strip().replace(" ", "")
    if not (len(digits) == QRR_ID_DIGITS and digits.isdigit()):
        raise ValueError(
            f"qr_reference_id {value!r} must be exactly {QRR_ID_DIGITS} digits — "
            f"it is the identification your bank assigns you (UBS: BESR-ID / "
            f"'ID number'), and it has to be the first six digits of every QR "
            f"reference you issue"
        )
    return digits


def make_qrr(number: str, identification: str | None = None) -> str:
    """The QR reference for `number`, prefixed with the bank's identification.

    Without an `identification` the prefix is six zeros: a valid reference the
    bank still credits, but one that skips the issuer identification banks ask
    for — configure `qr_reference_id` once and every reference carries it."""
    ref = compact_number(number)
    if not ref:
        raise ValueError(
            f"invoice number {number!r} has no alphanumeric character, so no "
            f"payment reference can be derived from it"
        )
    if len(ref) > QRR_MAX_CHARS:
        raise ValueError(
            f"invoice number {number!r} is {len(ref)} alphanumeric characters; a "
            f"QR reference (QRR) encodes at most {QRR_MAX_CHARS} of them in the "
            f"{_QRR_NUMBER_DIGITS} digits left beside the bank's six-digit "
            f"identification. Shorten the number, or invoice with a SCOR "
            f"reference (`reference: scor`), which carries up to "
            f"{SCOR_MAX_CHARS} characters as text"
        )
    prefix = check_qr_reference_id(identification) if identification else "0" * QRR_ID_DIGITS
    body = prefix + str(_encode(ref)).zfill(_QRR_NUMBER_DIGITS)
    return body + esr.calc_check_digit(body)


def legacy_qrr(number: str) -> str:
    """The pre-injective scheme: the number's digits, zero-padded to 26 + check.

    Kept so payments for invoices issued with it still find their invoice.
    New references are never minted this way."""
    digits = "".join(c for c in number if c.isdigit())[:26].rjust(26, "0")
    return digits + esr.calc_check_digit(digits)


def decode_qrr(reference: str) -> str | None:
    """The invoice number inside a QR reference, or None if it holds none.

    Works on any 27-digit reference with a valid check digit, whatever
    six-digit identification the issuer's bank assigned — so the matcher
    recognises the reference a customer's bank echoes back without knowing
    the prefix. A reference minted by another scheme decodes to a string that
    is simply not an invoice number, so callers must look the result up
    instead of trusting it."""
    digits = re.sub(r"\s", "", reference)
    if not digits.isdigit() or len(digits) > QRR_LENGTH:
        return None
    digits = digits.zfill(QRR_LENGTH)
    if esr.calc_check_digit(digits[:-1]) != digits[-1]:
        return None
    value = int(digits[QRR_ID_DIGITS : QRR_LENGTH - 1])
    return _decode(value) or None


# ── classification, formatting, resolution ────────────────────────────────────


def format_reference(kind: Kind, value: str) -> str:
    """Printed grouping: QRR `2 + 5×5`, SCOR in fours (QR-bill IG 3.6.2)."""
    return esr.format(value) if kind == "QRR" else iso11649.format(value)


def parse_reference(raw: str) -> PaymentReference:
    """Classify and fully validate a manually configured reference."""
    value = re.sub(r"[\s.\-/]", "", raw.upper())
    if value.startswith("RF"):
        try:
            value = iso11649.validate(value)
        except ValidationError as e:
            raise ValueError(
                f"reference {raw!r} is not a valid SCOR/ISO 11649 creditor "
                f"reference ({e}) — it is RF, two check digits, then up to "
                f"{SCOR_MAX_CHARS} alphanumeric characters"
            ) from None
        return PaymentReference("SCOR", value, iso11649.format(value))
    if value.isdigit():
        try:
            esr.validate(value)
        except ValidationError as e:
            raise ValueError(
                f"reference {raw!r} is not a valid Swiss QR reference (QRR) ({e}) "
                f"— it is {QRR_LENGTH - 1} digits plus a modulo-10 recursive "
                f"check digit"
            ) from None
        value = value.zfill(QRR_LENGTH)
        return PaymentReference("QRR", value, esr.format(value))
    raise ValueError(
        f"reference {raw!r} is neither a QR reference (all digits) nor a SCOR "
        f"creditor reference (RF…) — leave `reference` unset to let quints "
        f"derive it from the invoice number"
    )


def scheme_for(account: BankAccount, currency: str = "") -> Scheme:
    """Which scheme this account pays by: what it says, else what it can do.

    A regular IBAN alone can only carry SCOR, a QR-IBAN alone only QRR. An
    account with both has to say which: the choice decides which account the
    money lands in, and re-rendering an invoice must reproduce the reference
    the customer already holds — so quints refuses to guess."""
    if account.reference is not None:
        return account.reference
    if account.qr_iban and account.iban:
        where = f" for {currency}" if currency else ""
        raise ValueError(
            f"the bank account{where} has both `iban` and `qr_iban` but no "
            f"`reference:` — say which one its QR-bills use: `reference: scor` "
            f"(RF… creditor reference spelling out the invoice number, paid into "
            f"`iban`) or `reference: qrr` (numeric QR reference, paid into "
            f"`qr_iban`, needs the bank's `qr_reference_id`)"
        )
    return "qrr" if account.qr_iban else "scor"


def creditor_iban(account: BankAccount, kind: Kind, currency: str = "") -> str:
    """The IBAN a `kind` reference must be paid into — the schemes don't mix."""
    where = f" for {currency}" if currency else ""
    if kind == "QRR":
        if not account.qr_iban:
            raise ValueError(
                f"a QR reference (QRR) is only valid with a QR-IBAN, but the bank "
                f"account{where} has no `qr_iban` — add one, or drop "
                f"`reference: qrr` to invoice with a SCOR reference"
            )
        return account.qr_iban
    if not account.iban:
        raise ValueError(
            f"a SCOR creditor reference is only valid with a regular IBAN, but the "
            f"bank account{where} has none — add `iban:`, or set `reference: qrr` "
            f"(with the bank's `qr_reference_id`) to be paid into the QR-IBAN"
        )
    return account.iban


def payment_reference(inv: Invoice, account: BankAccount) -> PaymentReference:
    """The one place that decides how an invoice asks to be paid.

    An export invoice is settled by an ordinary credit transfer, which has no
    QR-bill and therefore no QRR — such an account's `reference: qrr` is
    overridden to SCOR rather than refused."""
    wanted: Kind = (
        "SCOR"
        if inv.kind == "export"
        else ("QRR" if scheme_for(account, inv.currency) == "qrr" else "SCOR")
    )
    if inv.reference:
        ref = parse_reference(inv.reference)
        if ref.kind != wanted:
            raise ValueError(
                f"invoice {inv.number} sets reference {inv.reference!r} ({ref.kind}), "
                f"but this invoice is paid with a {wanted} reference"
                + (
                    " — an ordinary credit transfer cannot carry a QR reference"
                    if inv.kind == "export"
                    else f" (the account resolves to `reference: {wanted.lower()}`)"
                )
            )
        return ref
    if wanted == "QRR":
        value = make_qrr(inv.number, account.qr_reference_id)
    else:
        value = make_scor(inv.number)
    return PaymentReference(wanted, value, format_reference(wanted, value))


# ── reading references out of a payment ───────────────────────────────────────


def references_in(text: str) -> list[str]:
    """Every structured reference a payment's text carries, normalised.

    Banks re-print references with their own spacing, so both forms are read
    space-tolerantly and then verified by their check digits — a candidate
    that fails is not a reference, and is dropped rather than guessed at."""
    up = text.upper()
    found: list[str] = []
    for m in re.finditer(r"RF\d{2}(?:[ \t]*[0-9A-Z]){1,40}", up):
        run = re.sub(r"\s+", "", m.group(0))
        body = run[4:]
        # The greedy run swallows whatever follows the reference ("RF80ACAD…B
        # VIELEN DANK"), so every length is tried from the longest down. All
        # that verify are kept, not just the first: mod-97-10 passes about one
        # arbitrary string in 97, so stopping at the first hit drops the real
        # reference in a few percent of payments. The caller looks each
        # candidate up, and a spurious one simply isn't an open invoice.
        for end in range(min(len(body), SCOR_MAX_CHARS), 0, -1):
            candidate = run[:4] + body[:end]
            if iso11649.is_valid(candidate):
                found.append(iso11649.compact(candidate))
    # 5 to 27 digits: some statement exports (and stdnum's own compact form)
    # drop the leading zeros, and every reference issued without a
    # qr_reference_id starts with six of them. A short run of digits that
    # happens to pass the check digit is simply looked up and misses.
    for m in re.finditer(r"(?<![0-9])(?:[0-9][ \t]*){4,26}[0-9](?![0-9])", up):
        digits = re.sub(r"\s+", "", m.group(0)).zfill(QRR_LENGTH)
        if esr.is_valid(digits):
            found.append(digits)
    return found


def numbers_in(text: str) -> list[str]:
    """Candidate invoice numbers in a payment's text, longest first.

    Whole tokens, then runs of two or three adjacent tokens joined — a payer
    who types ``ACAD 202608`` or ``ACAD-2026-08`` still means that invoice.
    Never substrings: a substring search credited a payment for
    ``ACAD202608B`` to ``ACAD202608``. Single tokens come first so an exact
    number always beats a join that happens to spell a longer one. The cost is
    that a number glued to other letters (``RGACAD202608``) is not seen — a
    missed match a human resolves, instead of a wrong one they never notice."""
    up = text.upper()
    singles: set[str] = set()
    for pattern in (r"[^0-9A-Z-]+", r"[^0-9A-Z]+"):
        for token in re.split(pattern, up):
            token = token.strip("-")
            if token:
                singles.add(token)
                singles.add(token.replace("-", ""))
    words = [w for w in re.split(r"[^0-9A-Z]+", up) if w]
    joins = {"".join(words[i : i + k]) for k in (2, 3) for i in range(len(words) - k + 1)} - singles
    by_length = lambda t: (-len(t), t)  # noqa: E731
    return sorted(singles, key=by_length) + sorted(joins, key=by_length)
