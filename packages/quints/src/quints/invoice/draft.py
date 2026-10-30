"""Ready-to-paste Beancount draft for an invoice with no ledger entry yet."""

from __future__ import annotations

from decimal import Decimal

from .. import config
from .model import Invoice, Totals, document_path, vat_included


def _quoted(value: str) -> str:
    """A beancount string literal — free text from the customer must not be
    able to break the draft out of its quotes."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def filing_account(inv: Invoice, cfg: config.Config) -> str:
    """The account the invoice books against, and its PDF is filed under: the
    income account — or, for a pro forma, the customer advances it becomes."""
    if inv.proforma:
        return cfg.advances_for(inv.kind)
    return cfg.income_export if inv.kind == "export" else cfg.income_domestic


def _vat_leg(cfg: config.Config, vat: Decimal, ccy: str, vat_chf: Decimal | None) -> str:
    """The OutputVAT amount: CHF as the return is, priced ``@@`` in `ccy`."""
    if ccy == "CHF":
        return f"{-vat:>10.2f} {ccy}"
    if vat_chf is not None:
        return f"{-vat_chf:>10.2f} CHF @@ {vat:.2f} {ccy}"
    return f"{-vat:>10.2f} {ccy}  ; TODO in CHF: quints vat convert"


def _lines(header: str, meta: list[str], legs: list[tuple[str, str, str]]) -> str:
    width = max(len(a) for a, _, _ in legs) + 4
    out = [header, *meta]
    for account, amount, posting_meta in legs:
        out.append(f"    {account:<{width}}{amount}")
        if posting_meta:
            out.append(f"      {posting_meta}")
    return "\n".join(out)


def vat_owed(inv: Invoice, totals: Totals) -> Decimal:
    """The VAT the draft books now: the pro forma's split out of its gross, or
    the invoice's VAT less what its advances declared on receipt."""
    if inv.proforma:
        return vat_included(totals.amount_due, totals.vat_rate)
    return totals.vat_amount - totals.advance_vat


def receipt_draft(
    inv: Invoice, totals: Totals, cfg: config.Config | None = None, vat_chf: Decimal | None = None
) -> str:
    """The booking for a pro forma's payment, when it arrives.

    Not income yet: the advance is a liability until the invoice credits it.
    The return declares it on receipt all the same — with its VAT when
    domestic (Art. 40 Abs. 1 Bst. c MWSTG), so the VAT is split out of the
    gross received the way the invoice will credit it back (`vat_included`).
    """
    cfg = cfg or config.get()
    ccy = inv.currency
    gross = totals.amount_due
    vat = vat_owed(inv, totals)
    doc = document_path(inv, filing_account(inv, cfg))
    legs = [(filing_account(inv, cfg), f"{-(gross - vat):>10.2f} {ccy}", "")]
    if vat:
        legs.append((cfg.output_vat, _vat_leg(cfg, vat, ccy, vat_chf), ""))
    header = (
        f"{inv.issue_date} * {_quoted(inv.resolved_customer.name)} "
        f"{_quoted(f'Advance on pro forma {inv.number}')} ^{inv.number}"
        "  ; TODO the day it was received"
    )
    meta = [
        f"    proforma: {_quoted(inv.number)}",
        f"    document: {_quoted(doc.name)}  ; TODO file the PDF under {doc.parent}/",
        f"    ; TODO the bank leg: {gross:.2f} {ccy} into the account it was paid to.",
    ]
    if vat:
        meta.append(
            "    ; A different amount arrived? Split that: VAT = amount × rate / (100 + rate)."
        )
    return _lines(header, meta, legs)


def build_draft(
    inv: Invoice,
    totals: Totals,
    cfg: config.Config | None = None,
    vat_chf: Decimal | None = None,
) -> str:
    """A balanced receivable booking matching what `verify.cross_check` expects.

    VAT is owed in CHF, so on a foreign-currency invoice the OutputVAT leg is
    ``vat_chf`` CHF, priced ``@@`` at the invoice's VAT. Without a rate
    (``vat_chf`` None) it stays in the invoice currency, marked to convert.
    """
    cfg = cfg or config.get()
    if inv.proforma:
        return receipt_draft(inv, totals, cfg, vat_chf)
    customer = inv.resolved_customer
    ccy = inv.currency
    income = filing_account(inv, cfg)
    advances = cfg.advances_for(inv.kind)
    narration = f"{inv.supply.text(inv.locale)} invoiced"
    doc = document_path(inv, income)  # same name the rendered PDF is filed under
    vat = vat_owed(inv, totals)

    legs: list[tuple[str, str, str]] = [(income, f"{-totals.subtotal:>10.2f} {ccy}", "")]
    if totals.amount_due:
        legs.append((cfg.receivable, f"{totals.amount_due:>10.2f} {ccy}", ""))
    for a in inv.advances:
        # Released net of its VAT, the way `receipt_draft` booked it. Posting
        # metadata, not a second ^link: receivables trust a lone invoice link.
        net = a.amount - vat_included(a.amount, totals.vat_rate)
        legs.append((advances, f"{net:>10.2f} {ccy}", f"proforma: {_quoted(a.proforma)}"))
    if vat:
        legs.append((cfg.output_vat, _vat_leg(cfg, vat, ccy, vat_chf), ""))
    if totals.rounding:
        legs.append((cfg.rounding_income, f"{-totals.rounding:>10.2f} {ccy}", ""))

    meta = [f"    invoice: {_quoted(inv.number)}"]
    if inv.customer_reference:
        # The payer quotes their own reference, not ours, when they ask about
        # this invoice — so the ledger can be searched by it too.
        meta.append(f"    customer_reference: {_quoted(inv.customer_reference)}")
    meta.append(f"    document: {_quoted(doc.name)}  ; TODO file the PDF under {doc.parent}/")
    header = f"{inv.issue_date} * {_quoted(customer.name)} {_quoted(narration)} ^{inv.number}"
    return _lines(header, meta, legs)
