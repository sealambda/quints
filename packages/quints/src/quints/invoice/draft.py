"""Ready-to-paste Beancount draft for an invoice with no ledger entry yet."""

from __future__ import annotations

from .. import config
from .model import Invoice, Totals, document_path


def _quoted(value: str) -> str:
    """A beancount string literal — free text from the customer must not be
    able to break the draft out of its quotes."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_draft(inv: Invoice, totals: Totals, cfg: config.Config | None = None) -> str:
    """A balanced receivable booking matching what `verify.cross_check` expects."""
    cfg = cfg or config.get()
    customer = inv.resolved_customer
    ccy = inv.currency
    income = cfg.income_export if inv.kind == "export" else cfg.income_domestic
    narration = f"{inv.supply} invoiced".strip() if inv.supply else f"Invoice {inv.number}"
    doc = document_path(inv, income)  # same name the rendered PDF is filed under

    legs: list[tuple[str, str]] = [(income, f"{-totals.subtotal:>10.2f} {ccy}")]
    legs.append((cfg.receivable, f"{totals.grand_total:>10.2f} {ccy}"))
    if totals.vat_amount:
        legs.append((cfg.output_vat, f"{-totals.vat_amount:>10.2f} {ccy}"))
    if totals.rounding:
        legs.append((cfg.rounding_income, f"{-totals.rounding:>10.2f} {ccy}"))

    width = max(len(a) for a, _ in legs) + 4
    lines = [
        f"{inv.issue_date} * {_quoted(customer.name)} {_quoted(narration)} ^{inv.number}",
        f"    invoice: {_quoted(inv.number)}",
    ]
    if inv.customer_reference:
        # The payer quotes their own reference, not ours, when they ask about
        # this invoice — so the ledger can be searched by it too.
        lines.append(f"    customer_reference: {_quoted(inv.customer_reference)}")
    lines.append(f"    document: {_quoted(doc.name)}  ; TODO file the PDF under {doc.parent}/")
    lines += [f"    {a:<{width}}{amt}" for a, amt in legs]
    return "\n".join(lines)
