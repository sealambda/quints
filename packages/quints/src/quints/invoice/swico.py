"""Swico S1 billing information for the QR-bill's structured data element.

The QR-bill carries two message fields. The *unstructured message* is what
the bank forwards to the creditor on the statement — quints puts the invoice
number there. The *billing information* (StrdBkgInf) is structured data for
the payer's accounts-payable software: it is **not** forwarded with the
payment, it exists so the payer's system can book the invoice without
re-typing it. Its syntax is Swico's S1 (v1.2, 23.11.2018):

``//S1`` then ``/nn/value`` tags, ascending, each at most once, tags without
data omitted:

===== ==========================================================
``10`` invoice number
``11`` invoice date, ``YYMMDD``
``20`` customer reference — the payer's PO / order number
``30`` the issuer's UID, digits only (``CHE-106.017.086 MWST`` → ``106017086``)
``31`` VAT date — the supply: ``YYMMDD`` for a day, ``YYMMDDYYMMDD`` for a period
``32`` VAT rate on the whole invoice, e.g. ``8.1``
``33`` pure import VAT (not applicable to an invoice quints issues)
``40`` payment conditions, ``discount%:days`` — ``0:30`` is net 30 days
===== ==========================================================

Values must not contain ``/`` or ``\\``; both are escaped. The unstructured
message and the billing information together may not exceed 140 characters,
so optional tags are dropped in a defined order rather than overflowing —
only an invoice number that alone does not fit is refused.
"""

from __future__ import annotations

import warnings
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .model import Invoice, Issuer, Totals

MAX_LENGTH = 140  # additional information + billing information, per the IG
# Dropped in this order when the 140 characters run out: the payer's software
# can live without payment conditions, VAT details and even the issuer's UID
# long before it can live without its own reference or the invoice number.
_DROP_ORDER = ("40", "32", "31", "30", "20", "11")


def escape(value: str) -> str:
    """`\\` then `/` — the other order would escape its own backslashes."""
    return value.replace("\\", "\\\\").replace("/", "\\/")


def _rate(value: Decimal) -> str:
    """A VAT rate as S1 spells it: `.` separator, no trailing zeros (`8.1`, `0`)."""
    return f"{value.normalize():f}"


def billing_information(
    inv: Invoice, issuer: Issuer, totals: Totals, message: str = ""
) -> str | None:
    """The `//S1/…` element for `inv`, or None if there is nothing to say.

    `message` is the unstructured message that shares the 140-character
    budget (quints sends the invoice number there)."""
    from . import vatid

    uid = vatid.swiss_uid_digits(issuer.vat_id) if issuer.country.upper() == "CH" else None
    tags: dict[str, str] = {
        "10": inv.number,
        "11": inv.issue_date.strftime("%y%m%d"),
        "20": inv.customer_reference or "",
        "30": uid or "",
        "31": inv.supply.swico(),
        "32": _rate(totals.vat_rate) if inv.kind == "domestic" else "",
        "40": f"0:{inv.terms_days}" if inv.terms_days is not None else "",
    }
    if not tags["10"]:
        return None
    while True:
        rendered = "//S1" + "".join(f"/{t}/{escape(v)}" for t, v in tags.items() if v)
        if len(rendered) + len(message) <= MAX_LENGTH:
            return rendered
        droppable = next((t for t in _DROP_ORDER if tags[t]), None)
        if droppable == "20":
            # The one tag the payer's side matches on — losing it is not silent.
            warnings.warn(
                f"invoice {inv.number}: the QR-bill's billing information has no room "
                f"left for customer_reference {inv.customer_reference!r} within "
                f"{MAX_LENGTH} characters — it stays on the printed invoice but not in "
                f"the payer's structured data; shorten it or the invoice number",
                stacklevel=2,
            )
        if droppable is None:
            raise ValueError(
                f"the QR-bill's structured billing information for invoice "
                f"{inv.number} does not fit in {MAX_LENGTH} characters even with "
                f"nothing but the invoice number in it — shorten the number"
            )
        tags[droppable] = ""
