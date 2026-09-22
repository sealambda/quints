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

The QRR body is therefore ``<6-digit bank id><20-digit encoded number>``, and
the encoding keeps the number as legible as twenty digits allow. A number of
the usual shape — up to four letters, then digits (``INV2026014``,
``ACAD202608``, ``20260001``) — is written as ``1``, a 7-digit block holding
the letters (bijective base 26) and the digit count, then the digits
themselves, right-aligned: ``INV2026014`` → ``1 0084117 000002026014``, so
the payment part and the bank statement end in ``…2026014``. Any other shape
(``ACAD202608B``) is written as ``2`` + 19 digits of bijective base 36. Both
are injective — ``INV0042`` ≠ ``INV42`` — and twelve characters is the most
either carries; a longer number is refused instead of silently truncated,
because a truncated reference is a payment credited to the wrong invoice.
``decode_qrr`` inverts both without knowing the issuer's six-digit id, which
is what lets the matcher recognise a reference it never generated. (The
Guidelines also forbid a reference of nothing but zeros; the leading variant
digit rules that out.)

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
_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
QRR_LENGTH = 27  # 26 body + 1 check digit
QRR_ID_DIGITS = 6  # the bank-assigned identification, first
_QRR_NUMBER_DIGITS = 20  # what is left for the encoded invoice number
QRR_MAX_CHARS = 12  # alphanumeric characters either encoding carries
# The legible form: up to four letters, then the digits. Its 7-digit head packs
# the letters' bijective-base-26 value beside the digit count (modulus 13
# covers counts 1–12), so leading zeros in the digit part survive the trip.
_LEGIBLE = re.compile(r"([A-Z]{0,4})([0-9]{1,12})")
_COUNT_MODULUS = 13
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


def _bijective(text: str, alphabet: str) -> int:
    """Bijective numeration — injective over strings, leading zeros included."""
    value = 0
    for ch in text:
        value = value * len(alphabet) + alphabet.index(ch) + 1
    return value


def _unbijective(value: int, alphabet: str) -> str:
    out: list[str] = []
    while value > 0:
        value -= 1
        out.append(alphabet[value % len(alphabet)])
        value //= len(alphabet)
    return "".join(reversed(out))


def _encode_number(ref: str) -> str:
    """The 20-digit body for a compact invoice number (≤ QRR_MAX_CHARS)."""
    m = _LEGIBLE.fullmatch(ref)
    if m:
        letters, digits = m.group(1), m.group(2)
        head = _bijective(letters, _LETTERS) * _COUNT_MODULUS + len(digits)
        return "1" + str(head).zfill(7) + digits.zfill(12)
    return "2" + str(_bijective(ref, _ALPHABET)).zfill(19)


def _decode_number(body: str) -> str | None:
    """Invert `_encode_number`; None unless `body` is a canonical encoding."""
    variant, rest = body[0], body[1:]
    if variant == "1":
        letters_value, count = divmod(int(rest[:7]), _COUNT_MODULUS)
        if not 1 <= count <= 12:
            return None
        number = _unbijective(letters_value, _LETTERS) + rest[7:][-count:]
    elif variant == "2":
        number = _unbijective(int(rest), _ALPHABET)
    else:
        return None
    # Round-trip: a legacy or foreign reference that happens to parse must not
    # come out as a plausible invoice number.
    return number if number and _encode_number(number) == body else None


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
    body = prefix + _encode_number(ref)
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
    return _decode_number(digits[QRR_ID_DIGITS : QRR_LENGTH - 1])


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


def scheme_for(account: BankAccount, currency: str) -> Scheme:
    """Which scheme this account pays by: what it says, else what it has.

    A QR-IBAN is the Swiss-native instrument — the payer's bank refuses any
    payment to it that lacks a valid QR reference — so an account that has one
    pays CHF invoices by QRR, whether or not a regular IBAN sits beside it.
    `reference: scor` is the explicit way to keep the QR-IBAN on file and still
    issue readable RF references into the IBAN. The QR scheme is CHF-only
    (Guidelines 2.10 and 2.12.1), so in any other currency an unset account
    falls back to SCOR."""
    if account.reference is not None:
        return account.reference
    if account.qr_iban and currency.upper() == "CHF":
        return "qrr"
    return "scor"


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
        way_out = (
            f" — a QR-IBAN cannot take {currency}: the QR scheme is CHF-only"
            if currency and currency.upper() != "CHF"
            else ", or set `reference: qrr` (with the bank's `qr_reference_id`) to be "
            "paid into the QR-IBAN"
        )
        raise ValueError(
            f"a SCOR creditor reference is only valid with a regular IBAN, but the "
            f"bank account{where} has none — add `iban:`{way_out}"
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
    # Reached only through an explicit `reference: qrr`: a QR-bill in EUR can
    # only be IBAN + SCOR (Guidelines 2.10, 2.12.1: QR-IBAN and QR reference
    # "can only be used for invoicing in CHF"). Refused rather than quietly
    # downgraded — the two schemes are paid into different accounts, so
    # switching would move the money without saying so.
    if wanted == "QRR" and inv.currency != "CHF":
        raise ValueError(
            f"invoice {inv.number} is in {inv.currency}, but its bank account is set "
            f"to `reference: qrr` — the Swiss Implementation Guidelines allow the QR "
            f"reference and the QR-IBAN for CHF only. A QR-bill in {inv.currency} is "
            f"paid into the regular `iban` with a SCOR reference: set "
            f"`reference: scor` on bank.{inv.currency}, or drop `reference:` and let "
            f"quints pick it"
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
