"""Ready-to-paste Beancount draft for an invoice with no ledger entry yet."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from .. import config, ledger
from .model import Invoice, Totals, document_path, vat_included
from .verify import Received


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


def _vat_leg(vat: Decimal, ccy: str, chf_rate: Decimal | None, todo: str = "") -> str:
    """The OutputVAT amount: CHF as the return is, priced ``@@`` in `ccy`."""
    if ccy == "CHF":
        return f"{-vat:>10.2f} {ccy}"
    if chf_rate is not None:
        return f"{-ledger.rappen(vat * chf_rate):>10.2f} CHF @@ {vat:.2f} {ccy}"
    return f"{-vat:>10.2f} {ccy}  ; TODO in CHF{todo}: quints vat convert"


def _lines(header: str, meta: list[str], legs: list[tuple[str, str, str]]) -> str:
    width = max(len(a) for a, _, _ in legs) + 4
    out = [header, *meta]
    for account, amount, posting_meta in legs:
        out.append(f"    {account:<{width}}{amount}")
        if posting_meta:
            out.append(f"      {posting_meta}")
    return "\n".join(out)


def receipt_draft(inv: Invoice, totals: Totals, cfg: config.Config | None = None) -> str:
    """The booking for a pro forma's payment, when it arrives.

    Not income yet: the advance is a liability until the invoice credits it.
    The return declares it on receipt all the same — with its VAT when
    domestic (Art. 40 Abs. 1 Bst. c MWSTG), so the VAT is split out of the
    gross received the way the invoice will credit it back (`vat_included`).
    """
    cfg = cfg or config.get()
    ccy = inv.currency
    gross = totals.amount_due
    vat = vat_included(gross, totals.vat_rate)
    doc = document_path(inv, filing_account(inv, cfg))
    legs = [(filing_account(inv, cfg), f"{-(gross - vat):>10.2f} {ccy}", "")]
    if vat:
        legs.append((cfg.output_vat, _vat_leg(vat, ccy, None, " at the day received"), ""))
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
    chf_rate: Decimal | None = None,
    received: Mapping[str, Received] | None = None,
) -> str:
    """A balanced receivable booking matching what `verify.cross_check` expects.

    VAT is owed in CHF, so on a foreign-currency invoice the OutputVAT leg is
    CHF at ``chf_rate`` (the issue date's), priced ``@@`` at the invoice's
    VAT. Without a rate it stays in the invoice currency, marked to convert.

    Credited advances are released as their receipts booked them
    (``received``, from `verify.cross_check`): the VAT they declared is not
    owed again, and a foreign advance leaves at the CHF it came in at — the
    income it becomes is valued at that too, so the advances account clears
    in CHF as well as in units. A pro forma missing from ``received`` is
    released as the invoice credits it.
    """
    cfg = cfg or config.get()
    if inv.proforma:
        return receipt_draft(inv, totals, cfg)
    customer = inv.resolved_customer
    ccy = inv.currency
    income = filing_account(inv, cfg)
    advances = cfg.advances_for(inv.kind)
    narration = f"{inv.supply.text(inv.locale)} invoiced"
    doc = document_path(inv, income)  # same name the rendered PDF is filed under

    vat = totals.vat_amount
    rest = totals.subtotal  # the income not advanced, valued at the issue date
    slices: list[tuple[str, str, str]] = []
    released: list[tuple[str, str, str]] = []
    for number in dict.fromkeys(a.proforma for a in inv.advances):
        r = (received or {}).get(number) or _as_credited(inv, number, totals.vat_rate)
        vat -= r.vat
        # Posting metadata, not a second ^link: receivables trust a lone invoice link.
        tag = f"proforma: {_quoted(number)}"
        if ccy != "CHF" and r.net_chf is not None:
            rest -= r.net
            slices.append((income, f"{-r.net:>10.2f} {ccy} @@ {r.net_chf:.2f} CHF", ""))
            released.append((advances, f"{r.net:>10.2f} {ccy} @@ {r.net_chf:.2f} CHF", tag))
        elif ccy != "CHF":
            todo = "  ; TODO @@ the CHF it was received at"
            released.append((advances, f"{r.net:>10.2f} {ccy}{todo}", tag))
        else:
            released.append((advances, f"{r.net:>10.2f} {ccy}", tag))

    legs = [*([(income, f"{-rest:>10.2f} {ccy}", "")] if rest else []), *slices]
    if totals.amount_due:
        legs.append((cfg.receivable, f"{totals.amount_due:>10.2f} {ccy}", ""))
    legs += released
    if vat:
        legs.append((cfg.output_vat, _vat_leg(vat, ccy, chf_rate), ""))
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


def _as_credited(inv: Invoice, number: str, rate: Decimal) -> Received:
    """An advance not (yet) in the books, split the way its receipt would be."""
    gross = sum((a.amount for a in inv.advances if a.proforma == number), Decimal("0"))
    vat = vat_included(gross, rate)
    return Received(net=gross - vat, vat=vat, net_chf=None)
