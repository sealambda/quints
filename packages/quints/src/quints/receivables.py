"""Open-invoice aging: Receivable movements grouped by invoice id.

An invoice id is the ``invoice:`` metadata or a ``^link`` that looks like an
invoice number (e.g. ACME202606). An invoice is open while its postings to
the receivable account don't net to zero — the same signal the balance
assertions in books/ rely on.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from beancount.core import data
from beancount.core import prices as bc_prices
from rich import box
from rich.console import Console
from rich.table import Table

from . import config, ledger, ui

# An invoice-shaped link: letters, then digits, then at most two trailing
# letters — a credit note's or re-issue's `B`. A real number like ACAD202608B
# must not fall out of receivables for carrying one. Nothing wider on purpose:
# `invoice_id` only trusts a *lone* invoice-shaped link, so a pattern that also
# took a hyphenated project or period link (`^PROJ2024-A`, `^FY2024-Q1`) would
# make the invoice booked beside it ambiguous and drop it instead.
_INVOICE_ID = re.compile(r"^[A-Z]{2,}[0-9]{4,}[A-Z]{0,2}$")
_TOL = Decimal("0.005")


@dataclass
class OpenInvoice:
    number: str
    payee: str
    invoice_date: Date
    currency: str
    open_amount: Decimal
    age_days: int


def invoice_id(e: data.Transaction) -> str | None:
    """The transaction's invoice id: `invoice:` metadata, else a lone
    invoice-shaped ^link."""
    meta = (e.meta or {}).get("invoice")
    if meta:
        return str(meta)
    links = [lk for lk in (e.links or ()) if _INVOICE_ID.match(lk)]
    return links[0] if len(links) == 1 else None


@dataclass
class _Group:
    """Mutable accumulator per (invoice number, currency)."""

    net: Decimal = Decimal("0")
    invoice_date: Date | None = None
    payee: str = ""


def compute_from_entries(
    entries: Sequence[data.Directive], at: Date, cfg: config.Config
) -> list[OpenInvoice]:
    groups: dict[tuple[str, str], _Group] = {}
    for e in entries:
        if not isinstance(e, data.Transaction) or e.date > at:
            continue
        txn_number = invoice_id(e)
        for p in e.postings:
            if p.account != cfg.receivable or p.units is None or p.units.number is None:
                continue
            # posting-level invoice: metadata wins — it lets one transaction
            # reallocate between invoices (e.g. a payment applied to the
            # wrong invoice, fixed by a zero-sum relink entry)
            posting_invoice = (p.meta or {}).get("invoice")
            number = str(posting_invoice) if posting_invoice else txn_number
            if number is None:
                continue
            g = groups.setdefault((number, p.units.currency), _Group())
            g.net += p.units.number
            if p.units.number > 0 and g.invoice_date is None:  # the invoicing leg
                g.invoice_date, g.payee = e.date, e.payee or ""

    out: list[OpenInvoice] = []
    for (number, currency), g in groups.items():
        if abs(g.net) <= _TOL:
            continue
        inv_date = g.invoice_date or at
        out.append(
            OpenInvoice(
                number=number,
                payee=g.payee,
                invoice_date=inv_date,
                currency=currency,
                open_amount=g.net,
                age_days=(at - inv_date).days,
            )
        )
    out.sort(key=lambda o: (o.invoice_date, o.number))
    return out


@dataclass
class CurrencyTotal:
    """One currency's open total and its value in the consolidation currency."""

    currency: str
    total: Decimal
    converted: Decimal | None  # None when no rate is available
    rate: Decimal | None
    rate_date: Date | None


@dataclass
class Consolidation:
    """Per-currency totals plus their sum in one consolidation currency."""

    currency: str
    totals: list[CurrencyTotal]
    grand_total: Decimal  # currencies without a rate excluded (see missing)
    missing: list[str]


def consolidate(
    open_invoices: Sequence[OpenInvoice],
    price_map: bc_prices.PriceMap,
    at: Date,
    currency: str,
) -> Consolidation:
    """Sum open invoices per currency and convert each total at the latest
    rate on/before ``at`` (the same price DB every other report uses)."""
    currency = currency.upper()
    sums: dict[str, Decimal] = {}
    for o in open_invoices:
        sums[o.currency] = sums.get(o.currency, Decimal("0")) + o.open_amount
    totals: list[CurrencyTotal] = []
    grand = Decimal("0")
    missing: list[str] = []
    for ccy in sorted(sums):
        rate_date, r = ledger.rate(price_map, ccy, at, quote=currency)
        converted = None
        if r is not None:
            converted = (sums[ccy] * Decimal(r)).quantize(Decimal("0.01"))
            grand += converted
        else:
            missing.append(ccy)
        totals.append(
            CurrencyTotal(
                currency=ccy,
                total=sums[ccy],
                converted=converted,
                rate=Decimal(r) if r is not None else None,
                rate_date=rate_date,
            )
        )
    return Consolidation(currency=currency, totals=totals, grand_total=grand, missing=missing)


def compute(
    ledger_path: Path,
    at: Date | None = None,
    cfg: config.Config | None = None,
    currency: str | None = None,
) -> tuple[list[OpenInvoice], Consolidation, Date]:
    cfg = cfg or config.get()
    at = at or datetime.now(timezone.utc).date()
    entries, _ = ledger.load_entries(ledger_path)
    open_invoices = compute_from_entries(entries, at, cfg)
    price_map = bc_prices.build_price_map(entries)
    consolidation = consolidate(open_invoices, price_map, at, currency or cfg.operating_currency)
    return open_invoices, consolidation, at


# ── render ────────────────────────────────────────────────────────────────────


def render(
    open_invoices: list[OpenInvoice],
    at: Date,
    consolidation: Consolidation | None = None,
    console: Console | None = None,
) -> None:
    console = console or ui.console
    console.print()
    console.rule(f"[bold]Open receivables[/]  ·  {at}")
    if not open_invoices:
        console.print("[ok]Nothing open — every invoice is settled.[/]")
        console.print()
        return

    t = Table(box=box.SIMPLE_HEAVY, pad_edge=False)
    t.add_column("Invoice", no_wrap=True)
    t.add_column("Payee")
    t.add_column("Date", no_wrap=True)
    t.add_column("Age", justify="right", no_wrap=True)
    t.add_column("Open", justify="right", no_wrap=True)
    for o in open_invoices:
        style = "owe" if o.age_days > 45 else ("warn" if o.age_days > 30 else "muted")
        t.add_row(
            o.number,
            o.payee,
            str(o.invoice_date),
            f"[{style}]{o.age_days} d[/]",
            f"{ui.money(o.open_amount)} {o.currency}",
        )
    t.add_section()
    if consolidation is None:
        totals: dict[str, Decimal] = {}
        for o in open_invoices:
            totals[o.currency] = totals.get(o.currency, Decimal("0")) + o.open_amount
        for ccy, total in sorted(totals.items()):
            t.add_row("[bold]Total[/]", "", "", "", f"[bold]{ui.money(total)} {ccy}[/]")
        console.print(t)
        console.print()
        return
    for ct in consolidation.totals:
        t.add_row("[bold]Total[/]", "", "", "", f"[bold]{ui.money(ct.total)} {ct.currency}[/]")
    # A consolidated row only earns its place when there is something to
    # consolidate: several currencies, or one that isn't the target itself.
    foreign = [ct for ct in consolidation.totals if ct.currency != consolidation.currency]
    if foreign:
        t.add_row(
            "[bold]≈ Total[/]",
            "",
            "",
            "",
            f"[bold]{ui.money(consolidation.grand_total)} {consolidation.currency}[/]",
        )
    console.print(t)
    if foreign and not consolidation.missing:
        rates = ", ".join(
            f"{ct.currency}→{consolidation.currency} {ct.rate} ({ct.rate_date})"
            for ct in foreign
            if ct.rate is not None
        )
        if rates:
            console.print(f"[muted]consolidated at {rates}[/]")
    for ccy in consolidation.missing:
        console.print(
            f"[warn]no {ccy}→{consolidation.currency} rate on or before {at} — "
            f"excluded from the consolidated total; run `quints prices sync`.[/]"
        )
    console.print()
