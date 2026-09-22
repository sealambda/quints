"""Open-bill aging: Payable movements grouped by bill id — the Kreditoren
half of :mod:`quints.receivables`.

A supplier bill is booked on receipt against the trade-payables account
(KMU 2000); the bank payment clears it. A bill is open while its postings to
that account don't net to zero — the same signal receivables use, mirrored
onto a liability, where the bill leg is the *credit* (negative) one.

Three things differ from receivables, because supplier documents are not
ours to number:

- **The grouping key** is the ``bill:`` metadata (the supplier's own invoice
  number), else a lone ``^link``. Neither is required: a bill booked with
  nothing to key on falls back to ``<payee> · <amount>``, which nets against
  a payment for the same payee and the same amount and nothing else. The
  fallback is reported in ``keyed_by`` so a reader can see what held the
  group together — two same-payee bills for the same amount share one
  synthetic key, and a partial payment against one of them does not net.
- **Aging is by due date**, not by document date: a bill's ``due:``
  metadata, else its date plus ``[payables] default_terms_days`` (30).
- **Open amounts are positive** — what you still owe — so the totals read
  the way an aging list is meant to read.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from beancount.core import data
from beancount.core import prices as bc_prices
from rich import box
from rich.console import Console
from rich.table import Table

from . import config, ledger, receivables, ui

_TOL = Decimal("0.005")

KEY_BILL = "bill"  # `bill:` metadata — the supplier's invoice number
KEY_LINK = "link"  # a lone ^link on the transaction
KEY_FALLBACK = "payee+amount"  # neither: grouped by payee and amount


@dataclass
class OpenBill:
    """One open supplier bill, aged against its due date."""

    number: str  # the grouping key: a bill number, or `<payee> · <amount>`
    keyed_by: str  # KEY_BILL | KEY_LINK | KEY_FALLBACK
    payee: str  # the supplier
    bill_date: Date
    due_date: Date
    currency: str
    open_amount: Decimal  # positive = still owed
    days_overdue: int  # at − due date; negative = not due yet


def bill_id(e: data.Transaction) -> tuple[str, str] | None:
    """The transaction's bill id and how it was found, or None for neither.

    ``bill:`` metadata first — it is the supplier's own invoice number, the
    one their payment reference spells out. Otherwise a *lone* ``^link``:
    with two links there is no telling which one names the bill, so such a
    transaction takes the (payee, amount) fallback instead of guessing.

    Any lone link counts, unlike :func:`quints.receivables.invoice_id`, which
    trusts only an invoice-shaped one. We number our invoices; suppliers
    number theirs however they like (``4711``, ``RE-2026/08``, ``2026-0042``),
    so there is no shape to require. The cost is the mirror image: on a
    transaction touching the payables account, a lone ``^PROJ2024-A`` *is*
    read as the bill id. Set ``bill:`` — which always wins — whenever a bill
    or its payment carries a link that doesn't name it.
    """
    meta = (e.meta or {}).get("bill")
    if meta:
        return str(meta), KEY_BILL
    links = list(e.links or ())
    return (links[0], KEY_LINK) if len(links) == 1 else None


def fallback_key(payee: str | None, amount: Decimal) -> str:
    """The key for a bill with no ``bill:`` and no lone link.

    Deliberately not number-shaped: it says out loud that the group is held
    together by the payee and the amount, nothing stronger. The amount is
    written to the Rappen so a bill booked ``-480.0`` and its payment booked
    ``480.00`` still land in the same group."""
    return f"{payee or '?'} · {abs(amount):.2f}"


def due_date(e: data.Transaction, cfg: config.Config) -> Date:
    """A bill's due date: ``due:`` metadata, else date + default terms."""
    raw = (e.meta or {}).get("due")
    if isinstance(raw, Date):
        return raw
    if raw is not None:
        try:
            return Date.fromisoformat(str(raw))
        except ValueError:
            pass  # unparseable `due:` — fall through to the configured terms
    return e.date + timedelta(days=cfg.payables_default_terms_days)


def _key_for(e: data.Transaction, p: data.Posting, amount: Decimal) -> tuple[str, str]:
    """The group key for one posting on the payables account.

    Posting-level ``bill:`` wins — one transaction can then settle or
    reallocate several bills (a lump-sum payment, a supplier credit note
    applied to the next bill)."""
    posting_bill = (p.meta or {}).get("bill")
    if posting_bill:
        return str(posting_bill), KEY_BILL
    txn_key = bill_id(e)
    if txn_key is not None:
        return txn_key
    return fallback_key(e.payee, amount), KEY_FALLBACK


def booked_bill(e: data.Transaction, cfg: config.Config) -> str | None:
    """The bill key of a booked supplier bill, or None if this isn't one.

    A bill is the transaction that *credits* the payables account; a payment
    debits it. Used to tell a booked bill apart from every other booking."""
    for p in e.postings:
        if p.account != cfg.payable or p.units is None or p.units.number is None:
            continue
        if p.units.number < 0:
            return _key_for(e, p, p.units.number)[0]
    return None


@dataclass
class _Group:
    """Mutable accumulator per (bill key, currency)."""

    net: Decimal = Decimal("0")
    bill_date: Date | None = None
    due: Date | None = None
    payee: str = ""
    keyed_by: str = ""  # set by the first posting, refined by the billing leg


def compute_from_entries(
    entries: Sequence[data.Directive], at: Date, cfg: config.Config
) -> list[OpenBill]:
    groups: dict[tuple[str, str], _Group] = {}
    for e in entries:
        if not isinstance(e, data.Transaction) or e.date > at:
            continue
        for p in e.postings:
            if p.account != cfg.payable or p.units is None or p.units.number is None:
                continue
            key, keyed_by = _key_for(e, p, p.units.number)
            g = groups.setdefault((key, p.units.currency), _Group())
            g.keyed_by = g.keyed_by or keyed_by
            g.net += p.units.number
            if p.units.number < 0 and g.bill_date is None:  # the billing leg (a credit)
                g.bill_date, g.payee = e.date, e.payee or ""
                g.due, g.keyed_by = due_date(e, cfg), keyed_by

    out: list[OpenBill] = []
    for (key, currency), g in groups.items():
        if abs(g.net) <= _TOL:
            continue
        billed = g.bill_date or at
        due = g.due or billed + timedelta(days=cfg.payables_default_terms_days)
        out.append(
            OpenBill(
                number=key,
                keyed_by=g.keyed_by,
                payee=g.payee,
                bill_date=billed,
                due_date=due,
                currency=currency,
                open_amount=-g.net,
                days_overdue=(at - due).days,
            )
        )
    out.sort(key=lambda b: (b.due_date, b.number))
    return out


def compute(
    ledger_path: Path,
    at: Date | None = None,
    cfg: config.Config | None = None,
    currency: str | None = None,
) -> tuple[list[OpenBill], receivables.Consolidation, Date]:
    cfg = cfg or config.get()
    at = at or datetime.now(timezone.utc).date()
    entries, _ = ledger.load_entries(ledger_path)
    open_bills = compute_from_entries(entries, at, cfg)
    price_map = bc_prices.build_price_map(entries)
    consolidation = receivables.consolidate(
        open_bills, price_map, at, currency or cfg.operating_currency
    )
    return open_bills, consolidation, at


# ── render ────────────────────────────────────────────────────────────────────


def _overdue(days: int) -> str:
    if days > 0:
        return f"[owe]{days} d[/]"
    if days == 0:
        return "[warn]today[/]"
    style = "warn" if days > -7 else "muted"
    return f"[{style}]in {-days} d[/]"


def render(
    open_bills: list[OpenBill],
    at: Date,
    consolidation: receivables.Consolidation | None = None,
    console: Console | None = None,
) -> None:
    console = console or ui.console
    console.print()
    console.rule(f"[bold]Open payables[/]  ·  {at}")
    if not open_bills:
        console.print("[ok]Nothing open — every supplier bill is paid.[/]")
        console.print()
        return

    t = Table(box=box.SIMPLE_HEAVY, pad_edge=False)
    t.add_column("Bill", no_wrap=True)
    t.add_column("Supplier")
    t.add_column("Due", no_wrap=True)
    t.add_column("Overdue", justify="right", no_wrap=True)
    t.add_column("Open", justify="right", no_wrap=True)
    for b in open_bills:
        t.add_row(
            b.number,
            b.payee,
            str(b.due_date),
            _overdue(b.days_overdue),
            f"{ui.money(b.open_amount)} {b.currency}",
        )
    t.add_section()
    receivables.render_totals(t, open_bills, consolidation, at, console)
