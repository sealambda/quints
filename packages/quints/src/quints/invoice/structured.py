"""The invoice as structured data: one neutral model every format serializes.

Factur-X, FatturaPA and a Verifactu record all want the same facts in
different XML. They are gathered here once, named after the business terms of
EN 16931 (BT-… / BG-…), which gives every field a public definition.

No recomputation: every amount is one `compute` produced and the PDF prints.
A credit note carries them unsigned — its document type says it credits.

Swiss facts in an EU vocabulary (the VAT category codes are UNTDID 5305):

- Swiss VAT charged → ``S`` with the Swiss rate. EN 16931 has no notion of
  whose VAT it is; the seller's `CHE-… MWST` number says so.
- Export with the reverse-charge mention → ``AE``, VAT to be accounted for by
  the buyer, both VAT numbers given.
- Anything else without VAT — an export outside a reverse-charge regime, an
  issuer not registered for VAT → ``O``, not subject to VAT. EN 16931 then
  forbids VAT identifiers (BR-O-02), so the seller's UID travels as its legal
  registration (BT-30) and the buyer's VAT number is left out.
- Pass-through items → a document-level charge, exempt (``E``) beside taxed
  lines; beside ``AE``/``O`` lines in their category, which may not be mixed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from . import reference as ref_mod
from .labels import labels
from .model import Invoice, Issuer, Totals


@dataclass(frozen=True)
class TradeParty:
    """BG-4 seller / BG-7 buyer."""

    name: str  # BT-27 / BT-44
    address: tuple[str, ...]  # BT-35… / BT-50…, as printed
    country: str  # BT-40 / BT-55
    vat_id: str | None = None  # BT-31 / BT-48
    legal_id: str | None = None  # BT-30, seller only


@dataclass(frozen=True)
class Tax:
    """A VAT category and rate (BT-151/152, BT-118/119)."""

    category: str  # S, AE, O or E
    rate: Decimal | None  # per cent; none when outside the scope of VAT (BR-O-05)
    reason: str | None = None  # BT-120, required unless S


@dataclass(frozen=True)
class Line:
    """BG-25."""

    id: str  # BT-126
    name: str  # BT-153
    quantity: Decimal  # BT-129
    unit_price: Decimal  # BT-146
    allowance: Decimal  # BT-136, the line's discount
    net: Decimal  # BT-131
    tax: Tax


@dataclass(frozen=True)
class AllowanceCharge:
    """A document-level allowance (BG-20) or charge (BG-21)."""

    charge: bool
    amount: Decimal  # BT-92 / BT-99
    reason: str  # BT-97 / BT-104
    tax: Tax


@dataclass(frozen=True)
class TaxBreakdown:
    """BG-23, one per category and rate."""

    tax: Tax
    basis: Decimal  # BT-116
    amount: Decimal  # BT-117


@dataclass(frozen=True)
class StructuredInvoice:
    number: str  # BT-1
    issue_date: date  # BT-2
    credit_note: bool  # BT-3: 381 rather than 380
    currency: str  # BT-5
    due_date: date | None  # BT-9
    order_reference: str | None  # BT-13
    preceding_invoice: str | None  # BT-25
    notes: tuple[str, ...]  # BT-22
    seller: TradeParty
    buyer: TradeParty
    period: tuple[date, date]  # BG-14
    iban: str  # BT-84
    bic: str | None  # BT-86
    payment_reference: str  # BT-83
    lines: tuple[Line, ...]
    allowances_charges: tuple[AllowanceCharge, ...]
    breakdown: tuple[TaxBreakdown, ...]
    line_total: Decimal  # BT-106
    allowance_total: Decimal  # BT-107
    charge_total: Decimal  # BT-108
    tax_basis_total: Decimal  # BT-109
    tax_total: Decimal  # BT-110
    grand_total: Decimal  # BT-112
    prepaid: Decimal  # BT-113
    rounding: Decimal  # BT-114
    due: Decimal  # BT-115


def _compact(vat_id: str) -> str:
    return "".join(ch for ch in vat_id.upper() if ch.isalnum())


def _uid(vat_id: str) -> str:
    """The company's UID without the suffix that makes it a VAT number."""
    return re.sub(r"(MWST|TVA|IVA|VAT)$", "", _compact(vat_id))


def build(
    inv: Invoice, issuer: Issuer, totals: Totals, *, reverse_charge: bool
) -> StructuredInvoice:
    """`inv` as rendered, in EN 16931 terms. Not for a pro forma: that is a
    request for payment, not an invoice."""
    lbl = labels(inv.language)
    sign = -1 if inv.credit_note_for else 1
    customer = inv.resolved_customer
    account = issuer.account(inv.currency)
    ref = ref_mod.payment_reference(inv, account)

    taxed = bool(totals.vat_groups)
    if taxed:
        untaxed = Tax("E", Decimal("0"), lbl["pass_through"])
    elif reverse_charge:
        untaxed = Tax("AE", Decimal("0"), lbl["reverse_charge"])
    else:
        untaxed = Tax(
            "O", None, lbl["export_note"] if inv.kind == "export" else "Not subject to VAT"
        )
    outside_scope = untaxed.category == "O"

    def line_tax(rate: Decimal | None) -> Tax:
        return Tax("S", rate if rate is not None else inv.vat_rate) if taxed else untaxed

    cent = Decimal("0.01")
    lines = tuple(
        Line(
            id=str(i),
            name=it.description,
            quantity=it.quantity,
            unit_price=it.unit_price,
            # What the line's discount took off, to the cent the line shows.
            allowance=(it.quantity * it.unit_price).quantize(cent, ROUND_HALF_UP) - it.total,
            net=it.total,
            tax=line_tax(it.vat_rate),
        )
        for i, it in enumerate(inv.items, 1)
    )

    # The invoice's discount per VAT rate: what stands between the lines of a
    # rate and the net its VAT was computed on.
    allowances: list[AllowanceCharge] = []
    breakdown: list[TaxBreakdown] = []
    if taxed:
        for g in totals.vat_groups:
            tax = Tax("S", g.rate)
            lines_net = sum((ln.net for ln in lines if ln.tax == tax), Decimal("0"))
            if lines_net != sign * g.net:
                allowances.append(
                    AllowanceCharge(False, lines_net - sign * g.net, lbl["discount"], tax)
                )
            breakdown.append(TaxBreakdown(tax, sign * g.net, sign * g.vat))
    else:
        if totals.discount:
            allowances.append(
                AllowanceCharge(False, sign * totals.discount, lbl["discount"], untaxed)
            )
    charges = [AllowanceCharge(True, p.amount, p.description, untaxed) for p in inv.pass_through]
    basis_untaxed = sign * totals.pass_through + (
        Decimal("0") if taxed else sign * (totals.subtotal - totals.discount)
    )
    if not taxed or charges:
        breakdown.append(TaxBreakdown(untaxed, basis_untaxed, Decimal("0")))

    payable = inv.terms_days is not None and totals.amount_due > 0
    return StructuredInvoice(
        number=inv.number,
        issue_date=inv.issue_date,
        credit_note=bool(inv.credit_note_for),
        currency=inv.currency,
        due_date=inv.issue_date + timedelta(days=inv.terms_days or 0) if payable else None,
        order_reference=inv.customer_reference,
        preceding_invoice=inv.credit_note_for,
        notes=tuple(inv.notes),
        seller=TradeParty(
            name=issuer.name,
            address=tuple(issuer.address),
            country=issuer.country,
            vat_id=_compact(issuer.vat_id) if issuer.vat_id and not outside_scope else None,
            legal_id=_uid(issuer.vat_id) if issuer.vat_id and outside_scope else None,
        ),
        buyer=TradeParty(
            name=customer.name,
            address=tuple(customer.address),
            country=customer.country,
            vat_id=_compact(customer.vat_id) if customer.vat_id and not outside_scope else None,
        ),
        period=(inv.supply.start, inv.supply.end),
        iban=ref_mod.creditor_iban(account, ref.kind, inv.currency),
        bic=account.bic,
        payment_reference=ref.value,
        lines=lines,
        allowances_charges=(*allowances, *charges),
        breakdown=tuple(breakdown),
        line_total=sign * totals.subtotal,
        allowance_total=sign * totals.discount,
        charge_total=sign * totals.pass_through,
        tax_basis_total=sign * (totals.subtotal - totals.discount + totals.pass_through),
        tax_total=sign * totals.vat_amount,
        grand_total=sign * totals.unrounded,
        prepaid=totals.advances,
        rounding=sign * totals.rounding,
        due=sign * totals.amount_due,
    )
