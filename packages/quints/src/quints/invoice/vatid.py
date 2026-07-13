"""Offline VAT-number validation via python-stdnum (per-country checksums).

Strategy: try the country's national module (stdnum.<cc>.vat or its alias,
e.g. ee.kmkr) and, for alpha-prefixed numbers, the EU-wide dispatcher. The
number is accepted if any applicable validator passes; countries without a
VAT module (e.g. US — no VAT regime) are accepted as-is. Online checks
(VIES/UID register) are deliberately out of scope here.
"""

from __future__ import annotations

from stdnum.eu import vat as euvat
from stdnum.exceptions import ValidationError
from stdnum.util import get_cc_module

_EU_PREFIX = {"GR": "EL"}  # country code → VAT prefix that differs


def validate(vat_id: str, country: str) -> None:
    """Raise ValueError if `vat_id` fails every applicable checksum for `country`."""
    cc = country.upper()
    compact = "".join(vat_id.split()).upper()
    errors: list[str] = []

    national = get_cc_module(cc.lower(), "vat")
    if national is not None:
        for candidate in (vat_id, _strip_prefix(compact, cc)):
            try:
                national.validate(candidate)
                return
            except ValidationError as e:
                errors.append(str(e))

    if compact[:2] == _EU_PREFIX.get(cc, cc) and compact[:2].isalpha():
        try:
            euvat.validate(compact)
            return
        except ValidationError as e:
            errors.append(str(e))

    if not errors:  # no validator knows this country → accept
        return
    raise ValueError(f"invalid VAT number {vat_id!r} for country {country}: {errors[0]}")


def _strip_prefix(compact: str, cc: str) -> str:
    prefix = _EU_PREFIX.get(cc, cc)
    return compact[2:] if compact.startswith(prefix) else compact
