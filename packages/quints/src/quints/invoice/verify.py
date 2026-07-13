"""Cross-check a rendered invoice against its ledger transaction."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from beancount.core import data

from .. import config, ledger
from .model import Invoice, Totals


@dataclass
class CrossCheck:
    found: bool
    date: str | None = None
    ledger_total: Decimal | None = None
    invoice_total: Decimal | None = None
    ok: bool = False
    date_ok: bool = True  # booking date equals the invoice issue date


def cross_check(ledger_path: Path, inv: Invoice, totals: Totals,
                tol: Decimal = Decimal("0.005")) -> CrossCheck:
    """Match by invoice id (metadata `invoice:` or a `^<number>` link); compare
    the invoice grand total to the income + output VAT across ALL matching
    transactions (the payment-clearing entry may carry the same link — its
    lack of income legs makes it contribute zero)."""
    cfg = config.get()
    entries, _ = ledger.load_entries(ledger_path)
    matches = []
    for e in entries:
        if not isinstance(e, data.Transaction):
            continue
        if inv.number == (e.meta or {}).get("invoice") or inv.number in (e.links or set()):
            matches.append(e)
    if not matches:
        return CrossCheck(found=False)

    expected = Decimal("0")
    booking = None  # the transaction that carries the income legs
    for e in matches:
        for p in e.postings:
            if p.units.currency != inv.currency:
                continue
            if p.account.startswith(cfg.income_prefix) or p.account == cfg.output_vat:
                expected += -p.units.number
                booking = booking or e
    booking = booking or matches[0]
    return CrossCheck(
        found=True,
        date=str(booking.date),
        ledger_total=expected,
        invoice_total=totals.grand_total,
        ok=abs(expected - totals.grand_total) <= tol,
        date_ok=booking.date == inv.issue_date,
    )
