"""IBAN and BIC hygiene for the account an invoice asks to be paid into.

A BIC is *not* computable from an IBAN. The IBAN carries a national institution
identifier — in CH/LI the 5-digit IID that SIX's bank master is keyed on — but
turning that into a BIC needs a registry that moves whenever institutions merge
or rebrand. A stale lookup would print a wrong-but-plausible BIC with the tool's
authority behind it, which is the exact failure this module exists to prevent.

So quints never guesses. It validates what the issuer configured, as hard as a
bank would, before the number can reach a PDF, and `quints iban` lets you check
a pair by hand.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from stdnum import bic as _bic
from stdnum import iban as _iban
from stdnum.exceptions import ValidationError

# CH and LI IBANs carry the 5-digit institution identifier (IID) straight after
# the check digits: CHkk IIII I... — the key into SIX's bank master data. (Named
# in prose, never linked: a rotted URL is worse than none in an error message.)
_IID_COUNTRIES = frozenset({"CH", "LI"})


def clean_iban(value: str, what: str = "iban") -> str:
    """Compact and fully validate an IBAN — length, national layout and mod-97
    check digits. Raises `ValueError` on anything a bank would bounce."""
    try:
        return _iban.validate(value)
    except ValidationError as exc:
        raise ValueError(f"{what} {value!r} is not a valid IBAN: {exc}") from None


def clean_bic(value: str) -> str:
    """Compact and validate a BIC/SWIFT code (ISO 9362)."""
    try:
        return _bic.validate(value)
    except ValidationError as exc:
        raise ValueError(
            f"bic {value!r} is not a valid BIC/SWIFT code: {exc} — a BIC is 8 or 11 "
            f"characters (4 bank, 2 country, 2 location, optional 3 branch)"
        ) from None


def format_iban(value: str) -> str:
    """Grouped in fours, the way it is printed on the invoice."""
    return _iban.format(value)


def iid(iban: str) -> str | None:
    """The 5-digit institution identifier of a CH/LI IBAN, else None."""
    compact = _iban.compact(iban)
    if compact[:2] not in _IID_COUNTRIES or len(compact) < 9:  # too short to carry one
        return None
    return compact[4:9]


@dataclass(frozen=True)
class Check:
    """What `quints iban` found about one payment instruction."""

    iban: str  # compact, as given (upper-cased, spaces dropped)
    formatted: str  # grouped in fours
    country: str | None
    iid: str | None  # CH/LI institution identifier
    bic: str | None  # compact, only when it parsed
    problems: list[str] = field(default_factory=list)  # a payer would be blocked
    notes: list[str] = field(default_factory=list)  # worth a human look

    @property
    def ok(self) -> bool:
        return not self.problems


def check(iban: str, bic: str | None = None) -> Check:
    """Check an IBAN, and a BIC against it, reporting instead of raising.

    Never invents a BIC: a missing one is reported as a problem to fix at the
    source, with the registry to look it up in."""
    problems: list[str] = []
    notes: list[str] = []

    compact = _iban.compact(iban)
    country = compact[:2] if len(compact) >= 2 and compact[:2].isalpha() else None
    try:
        compact = clean_iban(iban)
    except ValueError as exc:
        problems.append(str(exc))

    institution = iid(compact) if country in _IID_COUNTRIES else None

    bic_compact = None
    if bic:
        try:
            bic_compact = clean_bic(bic)
        except ValueError as exc:
            problems.append(str(exc))
    else:
        problems.append(
            "no BIC — a payer left to look one up can get it wrong and have the "
            "transfer returned; ask the bank that holds this account"
        )
        if institution:
            notes.append(f"the SIX bank master data lists the BIC for IID {institution}")

    if bic_compact and country and bic_compact[4:6] != country:
        # Legitimate for multi-currency providers (a Wise EUR account is a
        # Belgian IBAN under TRWIBEB1), so it is a look, not a blocker.
        notes.append(
            f"BIC country {bic_compact[4:6]} differs from IBAN country {country} — "
            f"normal for a payment provider, wrong if you pasted another account's BIC"
        )

    return Check(
        iban=compact,
        formatted=_iban.format(compact),
        country=country,
        iid=institution,
        bic=bic_compact,
        problems=problems,
        notes=notes,
    )
