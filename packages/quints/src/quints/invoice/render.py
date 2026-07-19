"""Render an invoice to PDF: build a data context, run Typst via typst-py."""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import TypedDict

import typst
from babel.dates import format_date

from . import qr
from .labels import labels as get_labels
from .model import Invoice, Issuer, Totals, compute, make_scor, money, number

TEMPLATE = Path(__file__).parent / "template.typ"


class InvoiceContext(TypedDict):
    """The data context handed to the Typst template as ``data.json``."""

    reverse_charge: bool
    lang: str
    kind: str
    currency: str
    labels: dict[str, str]
    issuer: dict[str, str | list[str] | None]
    customer: dict[str, str | list[str] | None]
    invoice: dict[str, str]
    terms: str | None
    items: list[dict[str, str | int]]
    totals: dict[str, str | bool]
    payment: Mapping[str, str | None]
    notes: list[str]
    brand: dict[str, str | int | None]


def _fmt_iban(iban: str) -> str:
    s = iban.replace(" ", "")
    return " ".join(s[i : i + 4] for i in range(0, len(s), 4))


def _fmt_date(d: date, locale: str) -> str:
    """CLDR medium date for the invoice locale (de_CH → 02.07.2026,
    es_ES → 2 jul 2026). The locale is validated on the model, so it resolves."""
    return format_date(d, format="medium", locale=locale)


def build_context(
    inv: Invoice,
    issuer: Issuer,
    totals: Totals,
    payment: Mapping[str, str | None],
    reverse_charge: bool = False,
    logo: str | None = None,
) -> InvoiceContext:
    lbl = get_labels(inv.language)
    customer = inv.resolved_customer
    return {
        "reverse_charge": reverse_charge,
        "lang": inv.language,
        "kind": inv.kind,
        "currency": inv.currency,
        "labels": lbl,
        "issuer": {
            "name": issuer.name,
            "address": issuer.address,
            "vat_id": issuer.vat_id,
            "country": issuer.country,
            "email": issuer.email,
            "phone": issuer.phone,
        },
        "customer": {
            "name": customer.name,
            "address": customer.address,
            "country": customer.country,
            "vat_id": customer.vat_id,
        },
        "invoice": {
            "number": inv.number,
            "supply": inv.supply,
            "issue_date": _fmt_date(inv.issue_date, inv.locale),
        },
        "terms": (lbl["terms"].format(days=inv.terms_days) if inv.terms_days is not None else None),
        "items": [
            {
                "pos": i + 1,
                "description": it.description,
                "quantity": number(it.quantity, inv.locale),
                "unit": it.unit,
                "unit_price": money(it.unit_price, inv.locale),
                "total": money(it.total, inv.locale),
            }
            for i, it in enumerate(inv.items)
        ],
        "totals": {
            "subtotal": money(totals.subtotal, inv.locale),
            "vat_rate": number(totals.vat_rate, inv.locale),
            "vat_amount": money(totals.vat_amount, inv.locale),
            "rounding": money(totals.rounding, inv.locale),
            "show_rounding": totals.rounding != 0,
            "grand_total": money(totals.grand_total, inv.locale),
            "export": inv.kind == "export",
        },
        "payment": payment,
        "notes": list(inv.notes),
        "brand": {
            "accent": issuer.brand.accent,
            "font": issuer.brand.font,
            "font_display": issuer.brand.font_display or issuer.brand.font,
            "display_stretch": issuer.brand.font_display_stretch,
            "logo": logo,
        },
    }


def _compile(main: Path, output: Path, root: Path, font_paths: list[Path]) -> None:
    """Compile to PDF/A-2b (archival) when supported, else a plain PDF."""
    fonts = [str(p) for p in font_paths]
    try:
        typst.compile(
            str(main), output=str(output), root=str(root), font_paths=fonts, pdf_standards="a-2b"
        )
    except (TypeError, ValueError):  # older typst-py, or standard unsupported
        typst.compile(str(main), output=str(output), root=str(root), font_paths=fonts)


def render(inv: Invoice, issuer: Issuer, out_path: Path) -> tuple[Path, Totals, str | None]:
    """Render to `out_path`. Returns (path, totals, qr_payload|None)."""
    totals = compute(inv)
    account = issuer.account(inv.currency)
    customer = inv.resolved_customer

    # Reverse charge needs a VAT-identified recipient: Art. 196 EU VAT Directive,
    # and Art. 226(4)+(11a) require the customer's VAT number + mention on the
    # invoice. Default for exports is reverse charge ON; opt out explicitly.
    reverse_charge = False
    if inv.kind == "export":
        reverse_charge = True if inv.reverse_charge is None else inv.reverse_charge
        if reverse_charge and not customer.vat_id:
            raise ValueError(
                f"export invoice {inv.number} claims reverse charge but customer "
                f"{customer.name!r} has no vat_id — add their VAT number, or "
                f"set `reverse_charge: false` if they are outside a reverse-charge "
                f"regime (Art. 196 / Art. 226(4) EU VAT Directive)"
            )
    work = Path(tempfile.mkdtemp(prefix="quints-inv-"))
    try:
        qr_payload = None
        if inv.kind == "domestic":
            # Swiss QR-bill: QR-IBAN + QRR if configured, else IBAN + SCOR.
            bill = qr.build_bill(inv, issuer, account, totals.grand_total)
            qr.write_svg(bill, work / "qrbill.svg")
            qr_payload = qr.payload(bill)
            payment = {"type": "qrbill"}
        else:
            # Cross-border: a QR-IBAN only works inside the QR-bill scheme —
            # foreign transfers need the regular IBAN.
            if not account.iban:
                raise ValueError(
                    f"export invoice in {inv.currency} needs a regular `iban` "
                    f"(a QR-IBAN cannot receive normal credit transfers)"
                )
            payment = {
                "type": "sepa",
                "iban": _fmt_iban(account.iban),
                "bic": account.bic,
                "reference": _fmt_iban(inv.reference or make_scor(inv.number)),
            }

        logo = issuer.brand.logo
        logo_name = None
        if logo and Path(logo).exists():
            dest = work / ("logo" + Path(logo).suffix)
            shutil.copy(logo, dest)
            logo_name = dest.name

        ctx = build_context(inv, issuer, totals, payment, reverse_charge, logo=logo_name)
        (work / "data.json").write_text(json.dumps(ctx, ensure_ascii=False, indent=2))
        shutil.copy(TEMPLATE, work / "template.typ")

        font_dir = issuer.brand.font_dir
        font_paths = [Path(font_dir)] if font_dir and Path(font_dir).is_dir() else []

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        _compile(work / "template.typ", out_path, work, font_paths)
        return out_path, totals, qr_payload
    finally:
        shutil.rmtree(work, ignore_errors=True)
