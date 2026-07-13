"""Ready-to-paste Beancount draft for an invoice with no ledger entry yet."""

from __future__ import annotations

import re

from .. import config
from .model import Invoice, Totals


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "invoice"


def build_draft(inv: Invoice, totals: Totals, cfg: config.Config | None = None) -> str:
    """A balanced receivable booking matching what `verify.cross_check` expects."""
    cfg = cfg or config.get()
    ccy = inv.currency
    income = cfg.income_export if inv.kind == "export" else cfg.income_domestic
    narration = f"{inv.supply} invoiced".strip() if inv.supply else f"Invoice {inv.number}"
    doc = f"{inv.issue_date}.{_slug(inv.customer.name)}.{_slug(inv.supply or inv.number)}.pdf"

    legs: list[tuple[str, str]] = [(cfg.receivable, f"{totals.grand_total:>10.2f} {ccy}")]
    legs.append((income, f"{-totals.subtotal:>10.2f} {ccy}"))
    if totals.vat_amount:
        legs.append((cfg.output_vat, f"{-totals.vat_amount:>10.2f} {ccy}"))
    if totals.rounding:
        legs.append((cfg.rounding_income, f"{-totals.rounding:>10.2f} {ccy}"))

    width = max(len(a) for a, _ in legs) + 4
    lines = [
        f'{inv.issue_date} * "{inv.customer.name}" "{narration}" ^{inv.number}',
        f'    invoice: "{inv.number}"',
        f'    document: "{doc}"  ; TODO file the PDF under documents/{income.replace(":", "/")}/',
    ]
    lines += [f"    {a:<{width}}{amt}" for a, amt in legs]
    return "\n".join(lines)
