"""Render an invoice to PDF: build a data context, run Typst via typst-py."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TypedDict, TypeVar

import typst
from babel import Locale
from babel.dates import format_date

from . import bank, epc, qr
from . import reference as ref_mod
from .labels import labels as get_labels
from .model import (
    Advance,
    BankAccount,
    Brand,
    Invoice,
    Issuer,
    LineItem,
    Problem,
    Problems,
    Totals,
    compute,
    money,
    number,
    vat_included,
)

TEMPLATE = Path(__file__).parent / "template.typ"
# Always on the search path: the default brand families (Geist, Geist Mono,
# Newsreader) plus Liberation Sans — the template's last-resort fallback and
# the only face the QR-bill payment part is allowed to use that we can ship.
# See fonts/README.md.
BUNDLED_FONTS = Path(__file__).parent / "fonts"
BUNDLED_FAMILIES = frozenset({"Geist", "Geist Mono", "Newsreader", "Liberation Sans"})


class InvoiceContext(TypedDict):
    """The data context handed to the Typst template as ``data.json``."""

    reverse_charge: bool
    proforma: bool
    lang: str
    region: str | None
    document: dict[str, str | list[str]]
    kind: str
    currency: str
    labels: dict[str, str]
    issuer: dict[str, str | list[str] | None]
    customer: dict[str, str | list[str] | None]
    invoice: dict[str, str]
    references: list[dict[str, str]]
    terms: str | None
    items: list[dict[str, str | int | None]]
    advances: list[dict[str, str]]
    pass_through: list[dict[str, str]]
    totals: dict[str, str | bool | list[dict[str, str]]]
    payment: Mapping[str, str | None]
    notes: list[str]
    brand: dict[str, str | float | bool | None]


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
    if inv.proforma:
        # Titled, numbered and totalled as what it is — never as "Rechnung".
        lbl = {
            **lbl,
            "invoice": lbl["proforma"],
            "invoice_no": lbl["proforma_no"],
            "date": lbl["proforma_date"],
            "grand_total": lbl["amount_due"],
        }
    if inv.credit_note_for:
        lbl = {
            **lbl,
            "invoice": lbl["credit_note"],
            "invoice_no": lbl["credit_note_no"],
            "date": lbl["proforma_date"],
            "grand_total": lbl["credit_total"],
        }
    sign = -1 if inv.credit_note_for else 1
    customer = inv.resolved_customer
    references = [
        # A credit note refers to the invoice it corrects (Art. 27 Abs. 4 MWSTG).
        *(
            [{"label": lbl["credit_note_for"], "value": inv.credit_note_for}]
            if inv.credit_note_for
            else []
        ),
        *(
            [{"label": lbl["your_reference"], "value": inv.customer_reference}]
            if inv.customer_reference
            else []
        ),
        *({"label": r.label, "value": r.value} for r in inv.references),
    ]
    # Nothing falls due on a credit note, or on an invoice advances covered.
    payable = inv.terms_days is not None and totals.amount_due > 0
    due = (
        _fmt_date(inv.issue_date + timedelta(days=inv.terms_days or 0), inv.locale)
        if payable
        else ""
    )
    return {
        "proforma": inv.proforma,
        "reverse_charge": reverse_charge,
        "lang": inv.language,
        "region": Locale.parse(inv.locale).territory,
        "document": _document_metadata(inv, issuer, totals, lbl, payment, references),
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
            # Postal convention: the country spelled out, in capitals, last line.
            "country_name": Locale.parse(inv.locale)
            .territories.get(customer.country, customer.country)
            .upper(),
            "vat_id": customer.vat_id,
        },
        "invoice": {
            "number": inv.number,
            "supply": inv.supply.text(inv.locale),
            "issue_date": _fmt_date(inv.issue_date, inv.locale),
            "due_date": due,
        },
        # The customer's own reference, then whatever else their side demands —
        # printed in the title meta grid, under the invoice number and date.
        "references": references,
        "terms": lbl["terms"].format(days=inv.terms_days) if payable else None,
        "items": [
            {
                "pos": i + 1,
                "description": it.description,
                "quantity": number(sign * it.quantity, inv.locale),
                "unit": it.unit,
                "unit_price": money(it.unit_price, inv.locale),
                "total": money(sign * it.total, inv.locale),
                "tag": _line_tag(inv, it, lbl, mixed=len(totals.vat_groups) > 1),
            }
            for i, it in enumerate(inv.items)
        ],
        "totals": _totals_context(inv, totals),
        # Outlays in the customer's name: their own block, after the VAT rows.
        "pass_through": [
            {"description": p.description, "amount": money(sign * p.amount, inv.locale)}
            for p in inv.pass_through
        ],
        # Credited advances, between the invoice total and the amount due.
        "advances": [
            {
                "label": _advance_label(inv, lbl, a, totals),
                "amount": money(-a.amount, inv.locale),
            }
            for a in inv.advances
        ],
        "payment": payment,
        "notes": [
            # A reissue says so: nothing about an issued invoice changes silently.
            *(
                [
                    lbl["corrected"].format(
                        date=_fmt_date(inv.corrected, inv.locale),
                        issued=_fmt_date(inv.issue_date, inv.locale),
                    )
                ]
                if inv.corrected
                else []
            ),
            *inv.notes,
        ],
        "brand": brand_context(issuer.brand, logo),
    }


def _line_tag(inv: Invoice, it: LineItem, lbl: Mapping[str, str], *, mixed: bool) -> str | None:
    """What a line says about itself after its description: its VAT rate when
    the invoice mixes rates, and its discount."""
    parts: list[str] = []
    if mixed:
        rate = it.vat_rate if it.vat_rate is not None else inv.vat_rate
        parts.append(f"{lbl['vat']} {number(rate, inv.locale)}%")
    if it.discount is not None:
        parts.append(f"{lbl['discount']} {number(it.discount, inv.locale)}%")
    return " · ".join(parts) or None


def _document_metadata(
    inv: Invoice,
    issuer: Issuer,
    totals: Totals,
    lbl: Mapping[str, str],
    payment: Mapping[str, str | None],
    references: list[dict[str, str]],
) -> dict[str, str | list[str]]:
    """The PDF's title, subject and keywords — what a mail client, file browser
    or the payer's accounts-payable system shows and indexes without opening
    the page. The title leads with the issuer: it is the tab title, and how a
    payer tells one supplier's PDF from the next."""
    due_label = lbl["amount_due"] if inv.advances else lbl["grand_total"]
    parts = [
        f"{lbl['invoice']} {inv.number}",
        inv.resolved_customer.name,
        f"{lbl['supply']} {inv.supply.text(inv.locale)}",
        f"{due_label} {inv.currency} {money(totals.amount_due, inv.locale)}",
    ]
    if inv.terms_days is not None and totals.amount_due > 0:
        due = inv.issue_date + timedelta(days=inv.terms_days)
        parts.append(f"{lbl['due_date']} {_fmt_date(due, inv.locale)}")
        parts.append(lbl["terms"].format(days=inv.terms_days))
    # Identifiers only: what a payer searches or matches the invoice on. The
    # payment reference compact, as it comes back on the bank statement.
    keywords = [inv.number, *(r["value"] for r in references)]
    if payment.get("reference"):
        keywords.append(str(payment["reference"]).replace(" ", ""))
    return {
        "title": f"{issuer.name} – {lbl['invoice']} {inv.number}",
        "description": " · ".join(parts),
        "keywords": keywords,
    }


def _advance_label(inv: Invoice, lbl: Mapping[str, str], a: Advance, totals: Totals) -> str:
    """The pro forma showed the advance's VAT, and the invoice shows VAT on the
    whole supply: naming the VAT credited back keeps it from reading as shown
    twice (Art. 27 Abs. 2 MWSTG)."""
    text = lbl["advance"].format(number=a.proforma, date=_fmt_date(a.received, inv.locale))
    if not totals.vat_rate:
        return text  # export or unregistered: no VAT was shown
    vat = money(vat_included(a.amount, totals.vat_rate), inv.locale)
    return f"{text}, {lbl['advance_vat'].format(vat=vat)}"


def _totals_context(inv: Invoice, totals: Totals) -> dict[str, str | bool | list[dict[str, str]]]:
    lbl = get_labels(inv.language)
    return {
        "subtotal": money(totals.subtotal, inv.locale),
        "discount": money(-totals.discount, inv.locale),
        "discount_label": lbl["discount"]
        + (
            f" {number(inv.discount.percent, inv.locale)}%"
            if inv.discount and inv.discount.percent
            else ""
        ),
        "show_discount": totals.discount != 0,
        "vat_rate": number(totals.vat_rate, inv.locale),
        "vat_amount": money(totals.vat_amount, inv.locale),
        # One row per rate (Art. 26 Abs. 2 Bst. f MWSTG).
        "vat_groups": [
            {
                "rate": number(g.rate, inv.locale),
                "net": money(g.net, inv.locale),
                "vat": money(g.vat, inv.locale),
            }
            for g in totals.vat_groups
        ],
        "rounding": money(totals.rounding, inv.locale),
        "show_rounding": totals.rounding != 0,
        "grand_total": money(totals.grand_total, inv.locale),
        "amount_due": money(totals.amount_due, inv.locale),
        "export": inv.kind == "export",
        # Not registered: no VAT row and no Swiss VAT note — the invoice
        # may not mention the tax at all (Art. 27 Abs. 1 MWSTG).
        "vat_registered": totals.vat_registered,
    }


def brand_context(brand: Brand, logo: str | None) -> dict[str, str | float | bool | None]:
    """The brand tokens a template reads (`logo` is the copied file's name)."""
    return {
        "accent": brand.accent,
        "font": brand.font,
        "font_display": brand.font_display or brand.font,
        "display_stretch": brand.font_display_stretch,
        "display_weight": brand.font_display_weight,
        "font_mono": brand.font_mono or brand.font,
        "ink": brand.ink,
        "subtle": brand.subtle,
        "rule": brand.rule,
        "panel": brand.panel,
        "logo": logo,
        "logo_height": brand.logo_height,
        "credit": brand.credit,
        "address_window": brand.address_window,
        "address_top": brand.address_top,
    }


def _compile(
    main: Path,
    output: Path,
    root: Path,
    font_paths: list[Path],
    only_bundled: bool,
    issued: date,
) -> None:
    """Compile to PDF/A-2a + PDF/UA-1 — never a silent plain-PDF fallback: an
    invoice is kept, readable, for ten years (Art. 958f OR, Art. 70 MWSTG).
    Level A (tagged, Unicode text, alt text on every image) over B: the archive
    copy stays searchable and its text extractable, and the payer's screen
    reader can read it. Not A-3: its only addition is embedded files, which
    only matters once a machine-readable invoice (Factur-X/ZUGFeRD) rides along.

    `only_bundled` switches machine-installed families off. Without it, a
    variable font installed on the designer's machine shadows the bundled (or
    issuer-shipped) static cuts of the same family and every weight silently
    collapses to the variable font's default instance, so the same invoice
    renders differently on a colleague's machine. Only an issuer who names a
    family that neither we nor they ship keeps system fonts on.

    The PDF is dated the issue date, not the moment of rendering: the same
    invoice then renders to the same bytes, so an archived copy can be checked
    by rendering it again.
    """
    typst.compile(
        str(main),
        output=str(output),
        root=str(root),
        font_paths=[str(p) for p in font_paths],
        ignore_system_fonts=only_bundled,
        pdf_standards=["a-2a", "ua-1"],
        timestamp=int(datetime(issued.year, issued.month, issued.day, tzinfo=UTC).timestamp()),
    )


# The suffix that makes a Swiss UID a VAT number (MWST / TVA / IVA, and the
# English VAT some issuers print). Only a registered issuer may carry it.
_VAT_SUFFIX = re.compile(r"\b(MWST|TVA|IVA|VAT)\b", re.IGNORECASE)


def check_vat_identity(issuer: Issuer, vat_registered: bool) -> None:
    """The issuer's VAT number must match its registration.

    A registered issuer has to print it (Art. 26 Abs. 2 Bst. a MWSTG); one
    that is not may not suggest it is — an invoice that looks like it carries
    VAT makes the tax owed (Art. 27 MWSTG).
    """
    if vat_registered and not issuer.vat_id:
        raise ValueError(
            f"{issuer.name} is VAT-registered but issuer.vat_id is empty — a Swiss invoice "
            "must carry the VAT number, e.g. CHE-123.456.789 MWST (Art. 26 Abs. 2 Bst. a MWSTG)"
        )
    if not vat_registered and issuer.vat_id and _VAT_SUFFIX.search(issuer.vat_id):
        raise ValueError(
            f"{issuer.name} is not VAT-registered on this date, but issuer.vat_id "
            f"{issuer.vat_id!r} reads as a VAT number — drop the suffix (keep the bare UID) "
            "or the vat_id; an invoice may not suggest VAT that isn't owed (Art. 27 MWSTG)"
        )


def _reverse_charge(inv: Invoice) -> bool:
    """Whether the invoice carries the reverse-charge mention.

    Reverse charge needs a VAT-identified recipient: Art. 196 EU VAT Directive,
    and Art. 226(4)+(11a) require the customer's VAT number + mention on the
    invoice. Default for exports is reverse charge ON; opt out explicitly.
    """
    if inv.kind != "export":
        return False
    reverse_charge = True if inv.reverse_charge is None else inv.reverse_charge
    customer = inv.resolved_customer
    if reverse_charge and not customer.vat_id:
        raise ValueError(
            f"export invoice {inv.number} claims reverse charge but customer "
            f"{customer.name!r} has no vat_id — add their VAT number, or "
            f"set `reverse_charge: false` if they are outside a reverse-charge "
            f"regime (Art. 196 / Art. 226(4) EU VAT Directive)"
        )
    return reverse_charge


def _sepa_payment(
    inv: Invoice, issuer: Issuer, account: BankAccount, amount: Decimal
) -> dict[str, str | None]:
    """The cross-border payment block: IBAN, BIC and reference — plus, in EUR,
    the EPC QR payload that pre-fills the same transfer in a banking app."""
    # Cross-border: a QR-IBAN only works inside the QR-bill scheme —
    # foreign transfers need the regular IBAN.
    if not account.iban:
        raise ValueError(
            f"export invoice in {inv.currency} needs a regular `iban` "
            f"(a QR-IBAN cannot receive normal credit transfers)"
        )
    # No BIC, no invoice. A payer who has to look the code up gets it
    # wrong sooner or later and the transfer comes back — the one
    # failure a printed invoice can prevent outright. It cannot be
    # derived from the IBAN, so it has to be configured.
    if not account.bic:
        raise ValueError(
            f"export invoice {inv.number} in {inv.currency} has no `bic` — "
            f"a SEPA/SWIFT payer left to look the code up can get it wrong "
            f"and have the payment returned. Add `bic:` under "
            f"bank.{inv.currency} in the issuer config; "
            f"`quints iban {account.iban}` shows what to ask the bank for"
        )
    beneficiary = account.holder or issuer.name
    ref = ref_mod.payment_reference(inv, account)
    # The EPC code is SEPA credit transfers in euro only; anything else is
    # paid from the printed fields.
    scan = inv.currency == "EUR" and amount > 0
    lbl = get_labels(inv.language)
    return {
        "type": "sepa",
        "beneficiary": beneficiary,
        "iban": bank.format_iban(account.iban),
        "bic": account.bic,
        "bank_name": account.bank_name,
        "reference": ref.formatted,
        "epc": epc.payload(beneficiary, account.iban, account.bic, amount, ref) if scan else None,
        # What the code pre-fills, for whoever cannot scan it.
        "alt": f"{lbl['scan_to_pay']} · {inv.currency} {money(amount, inv.locale)}"
        f" · {lbl['payment_to']} {beneficiary} · IBAN {bank.format_iban(account.iban)}"
        f" · {lbl['reference']} {ref.formatted}",
    }


_T = TypeVar("_T")


def check(inv: Invoice, issuer: Issuer, vat_registered: bool = True) -> Totals:
    """Everything that stops this invoice from rendering, raised together.

    Each check has a stable code and names the key to fix. One that depends on
    another's result (the payment block needs the bank account) is skipped
    when that one failed."""
    found: list[Problem] = []

    def attempt(code: str, field: str, fn: Callable[[], _T]) -> _T | None:
        try:
            return fn()
        except ValueError as e:
            found.append(Problem(code, field, str(e)))
            return None

    bank_key = f"issuer.bank.{inv.currency}"
    attempt("vat_identity", "issuer.vat_id", lambda: check_vat_identity(issuer, vat_registered))
    attempt("reverse_charge", "customer.vat_id", lambda: _reverse_charge(inv))
    totals = attempt("advances", "advances", lambda: compute(inv, vat_registered))
    account = attempt("bank_account", bank_key, lambda: issuer.account(inv.currency))
    if account is not None and totals is not None and totals.amount_due > 0:
        ok = attempt(
            "payment_reference", "reference", lambda: ref_mod.payment_reference(inv, account)
        )
        if ok is None:
            pass
        elif inv.kind == "domestic":
            attempt("qr_bill", bank_key, lambda: qr.build_bill(inv, issuer, account, totals))
        else:
            attempt(
                "payment_account",
                bank_key,
                lambda: _sepa_payment(inv, issuer, account, totals.amount_due),
            )
    if found or totals is None:
        raise Problems(found)
    return totals


def fonts(brand: Brand) -> tuple[list[Path], bool]:
    """(font search path, whether only the bundled families may be used)."""
    font_dir = brand.font_dir
    issuer_fonts = [Path(font_dir)] if font_dir and Path(font_dir).is_dir() else []
    # Ours first so the guaranteed fallback is always resolvable; the
    # issuer's own families win on name, not on search order.
    named = {brand.font, brand.font_display or brand.font, brand.font_mono or brand.font}
    return [BUNDLED_FONTS, *issuer_fonts], bool(issuer_fonts) or named <= BUNDLED_FAMILIES


def render(
    inv: Invoice, issuer: Issuer, out_path: Path, vat_registered: bool = True
) -> tuple[Path, Totals, str | None]:
    """Render to `out_path`. Returns (path, totals, qr_payload|None).

    ``vat_registered`` is the issuer's VAT status on the issue date (from
    quints.toml): when False, no VAT is charged or mentioned."""
    totals = check(inv, issuer, vat_registered)
    account = issuer.account(inv.currency)

    reverse_charge = _reverse_charge(inv)
    work = Path(tempfile.mkdtemp(prefix="quints-inv-"))
    try:
        qr_payload = None
        if totals.amount_due <= 0:
            # Advances covered it all, or it is a credit note: nothing to pay.
            payment: dict[str, str | None] = {"type": "none"}
        elif inv.kind == "domestic":
            # Swiss QR-bill: IBAN + SCOR, or QR-IBAN + QRR — see `reference.py`.
            bill = qr.build_bill(inv, issuer, account, totals)
            qr.write_svg(bill, work / "qrbill.svg")
            qr_payload = qr.payload(bill)
            ref = ref_mod.payment_reference(inv, account).formatted
            lbl = get_labels(inv.language)
            # The payment part is an image: its alt text is the only way a
            # screen reader, or text extraction, gets at what it says.
            alt = (
                f"{lbl['qr_bill']} · {inv.currency} {money(totals.amount_due, inv.locale)}"
                f" · {lbl['payment_to']} {account.holder or issuer.name}"
                f" · IBAN {bank.format_iban(bill.account)} · {lbl['reference']} {ref}"
            )
            payment = {"type": "qrbill", "reference": ref, "alt": alt}
        else:
            payment = _sepa_payment(inv, issuer, account, totals.amount_due)
            qr_payload = payment["epc"]
            if qr_payload:
                epc.write_svg(qr_payload, work / "epc.svg")

        logo = issuer.brand.logo
        logo_name = None
        if logo and Path(logo).exists():
            dest = work / ("logo" + Path(logo).suffix)
            shutil.copy(logo, dest)
            logo_name = dest.name

        ctx = build_context(inv, issuer, totals, payment, reverse_charge, logo=logo_name)
        (work / "data.json").write_text(json.dumps(ctx, ensure_ascii=False, indent=2))
        shutil.copy(TEMPLATE, work / "template.typ")

        font_paths, only_bundled = fonts(issuer.brand)
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        _compile(work / "template.typ", out_path, work, font_paths, only_bundled, inv.issue_date)
        return out_path, totals, qr_payload
    finally:
        shutil.rmtree(work, ignore_errors=True)
