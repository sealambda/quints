"""Swiss QR-bill construction via the qrbill library (domestic CHF invoices)."""

from __future__ import annotations

import re
import warnings
from collections.abc import Sequence
from pathlib import Path

from qrbill import QRBill

from . import reference as ref_mod
from . import swico
from .model import BankAccount, Invoice, Issuer, Totals


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


def build_bill(inv: Invoice, issuer: Issuer, account: BankAccount, totals: Totals) -> QRBill:
    customer = inv.resolved_customer
    currency = inv.currency
    if currency not in ("CHF", "EUR"):
        raise ValueError(f"Swiss QR-bill supports only CHF or EUR, not {currency!r}")
    payment_ref = ref_mod.payment_reference(inv, account)
    # The unstructured message is what the bank forwards on the creditor's
    # statement, so it carries the invoice number; the structured billing
    # information is for the payer's software and carries everything else.
    message = inv.number
    billing = swico.billing_information(inv, issuer, totals, message) or ""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        bill = QRBill(
            account=ref_mod.creditor_iban(account, payment_ref.kind, currency),
            creditor=_structured(issuer.name, issuer.address, issuer.country),
            debtor=_structured(customer.name, customer.address, customer.country),
            amount=f"{totals.grand_total:.2f}",
            currency=currency,
            reference_number=payment_ref.value,
            additional_information=message,
            billing_information=billing,
        )
    # The Implementation Guidelines allow Arial, Frutiger, Helvetica and
    # Liberation Sans in the payment part, and nothing else. qrbill asks for
    # Arial/Helvetica only, which are system faces — an issuer that bundles
    # fonts has system faces switched off, so name the OFL one it can ship.
    bill.font_family = "Arial,Helvetica,Liberation Sans"
    return bill


def write_svg(bill: QRBill, path: Path) -> None:
    with open(path, "w") as f:
        bill.as_svg(f)


def payload(bill: QRBill) -> str:
    return bill.qr_data()
