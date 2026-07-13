"""Open-invoice aging: Receivable movements grouped by invoice id.

An invoice id is the ``invoice:`` metadata or a ``^link`` that looks like an
invoice number (e.g. ACME202606). An invoice is open while its postings to
the receivable account don't net to zero — the same signal the balance
assertions in books/ rely on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from beancount.core import data
from rich import box
from rich.console import Console
from rich.table import Table

from . import config, ledger, ui

_INVOICE_ID = re.compile(r"^[A-Z]{2,}[0-9]{4,}$")
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


def compute_from_entries(entries, at: Date, cfg: config.Config) -> list[OpenInvoice]:
    # (number, currency) → [net, invoice_date, payee]
    groups: dict[tuple, list] = {}
    for e in entries:
        if not isinstance(e, data.Transaction) or e.date > at:
            continue
        txn_number = invoice_id(e)
        for p in e.postings:
            if p.account != cfg.receivable:
                continue
            # posting-level invoice: metadata wins — it lets one transaction
            # reallocate between invoices (e.g. a payment applied to the
            # wrong invoice, fixed by a zero-sum relink entry)
            number = (p.meta or {}).get("invoice") or txn_number
            if number is None:
                continue
            g = groups.setdefault((number, p.units.currency), [Decimal("0"), None, ""])
            g[0] += p.units.number
            if p.units.number > 0 and g[1] is None:  # the invoicing leg
                g[1], g[2] = e.date, e.payee or ""

    out = []
    for (number, currency), (net, inv_date, payee) in groups.items():
        if abs(net) <= _TOL:
            continue
        inv_date = inv_date or at
        out.append(
            OpenInvoice(
                number=number,
                payee=payee,
                invoice_date=inv_date,
                currency=currency,
                open_amount=net,
                age_days=(at - inv_date).days,
            )
        )
    out.sort(key=lambda o: (o.invoice_date, o.number))
    return out


def compute(
    ledger_path: Path, at: Date | None = None, cfg: config.Config | None = None
) -> tuple[list[OpenInvoice], Date]:
    cfg = cfg or config.get()
    at = at or datetime.now(timezone.utc).date()
    entries, _ = ledger.load_entries(ledger_path)
    return compute_from_entries(entries, at, cfg), at


# ── render ────────────────────────────────────────────────────────────────────


def render(open_invoices: list[OpenInvoice], at: Date, console: Console | None = None) -> None:
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
    totals: dict[str, Decimal] = {}
    for o in open_invoices:
        totals[o.currency] = totals.get(o.currency, Decimal("0")) + o.open_amount
    t.add_section()
    for ccy, total in sorted(totals.items()):
        t.add_row("[bold]Total[/]", "", "", "", f"[bold]{ui.money(total)} {ccy}[/]")
    console.print(t)
    console.print()
