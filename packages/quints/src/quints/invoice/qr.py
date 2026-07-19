"""Swiss QR-bill construction via the qrbill library (domestic CHF invoices)."""

from __future__ import annotations

import re
import warnings
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

from qrbill import QRBill

from .model import BankAccount, Invoice, Issuer, make_qrr, make_scor


def _structured(name: str, lines: Sequence[str], country: str) -> dict[str, str | None]:
    """Parse free-form address lines into qrbill's structured address dict."""
    lines = [ln.strip() for ln in lines if ln and ln.strip()]
    street = house = pcode = city = ""
    postal_idx = None
    for i, ln in enumerate(lines):
        m = re.match(r"^([A-Za-z]{0,3}[- ]?\d{3,6})\s+(.+)$", ln)  # e.g. "3000 Bern"
        if m:
            pcode, city, postal_idx = m.group(1), m.group(2), i
            break
    street_lines = [ln for i, ln in enumerate(lines) if i != postal_idx]
    if street_lines:
        m = re.match(r"^(.*?)[\s,]+(\d+\w*)$", street_lines[0])  # split trailing house no
        street, house = (m.group(1), m.group(2)) if m else (street_lines[0], "")
    return {
        "name": name,
        "street": street or None,
        "house_num": house or None,
        "pcode": pcode or "0",
        "city": city or name,
        "country": country,
    }


def reference_for(inv: Invoice, account: BankAccount) -> str | None:
    if inv.reference:
        return inv.reference
    if account.qr_iban:
        return make_qrr(inv.number)  # QRR — mandatory with a QR-IBAN
    return make_scor(inv.number)  # SCOR (ISO 11649) with a regular IBAN


def build_bill(inv: Invoice, issuer: Issuer, account: BankAccount, grand: Decimal) -> QRBill:
    customer = inv.resolved_customer
    currency = inv.currency
    if currency not in ("CHF", "EUR"):
        raise ValueError(f"Swiss QR-bill supports only CHF or EUR, not {currency!r}")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return QRBill(
            account=account.qr_iban or account.iban,
            creditor=_structured(issuer.name, issuer.address, issuer.country),
            debtor=_structured(customer.name, customer.address, customer.country),
            amount=f"{grand:.2f}",
            currency=currency,
            reference_number=reference_for(inv, account),
            additional_information=f"{inv.number}",
        )


def write_svg(bill: QRBill, path: Path) -> None:
    with open(path, "w") as f:
        bill.as_svg(f)


def payload(bill: QRBill) -> str:
    return bill.qr_data()
