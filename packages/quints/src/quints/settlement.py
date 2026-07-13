"""VAT settlement (period close) and outstanding-liability tracking.

A settlement crystallizes a period's accrued VAT into the PayableVAT liability:
it debits OutputVAT (303) and Bezugsteuer (382) and credits InputVAT (479),
leaving the net (500) owed to the ESTV. Payment follows later — Swiss VAT is due 60 days after period end
(Art. 86 MWSTG). Settlement and its eventual payment share a ``^VAT-<period>``
link, and the settlement carries a ``due:`` date, so outstanding liabilities can
be listed until paid.

Like `vat`, this only *prints* the transaction to paste — it never writes the
ledger. The emitted balance assertions make bean-check verify the flush.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from beancount.core import data
from rich import box
from rich.console import Console
from rich.table import Table

from . import config, ledger, ui
from .mwst import MwstReport

PAYMENT_DUE_DAYS = 60  # Art. 86 MWSTG


# ── settlement generation ─────────────────────────────────────────────────────


@dataclass
class Settlement:
    settle_date: str
    assert_date: str
    due: str
    link: str
    narration: str
    output_vat: Decimal
    input_vat: Decimal
    net: Decimal
    payable_after: Decimal
    bezugsteuer: Decimal = Decimal("0")


def _payable_balance(entries, upto: Date, cfg: config.Config) -> Decimal:
    bal = Decimal("0")
    for e in entries:
        if isinstance(e, data.Transaction) and e.date <= upto:
            for p in e.postings:
                if p.account == cfg.payable_vat:
                    bal += p.units.number
    return bal


def build_settlement(
    ledger_path: Path, report: MwstReport, label: str | None = None,
    cfg: config.Config | None = None,
) -> Settlement:
    d1 = Date.fromisoformat(report.date_to)
    label = label or f"{report.date_from}..{report.date_to}"
    link = "VAT-" + label.replace(" ", "")
    cfg = cfg or config.get()
    entries, _ = ledger.load_entries(ledger_path)
    payable_before = _payable_balance(entries, d1, cfg)  # excludes the (unbooked) settlement
    return Settlement(
        settle_date=str(d1),
        assert_date=str(d1 + timedelta(days=1)),
        due=str(d1 + timedelta(days=PAYMENT_DUE_DAYS)),
        link=link,
        narration=f"{label} VAT Settlement",
        output_vat=report.z303_tax,
        bezugsteuer=report.z382_tax,
        input_vat=report.z479,
        net=report.z500,
        payable_after=payable_before - report.z500,
    )


def _posting(account: str, amount: Decimal) -> str:
    return f"    {account:<38}{amount:>10.2f} CHF"


def settlement_text(s: Settlement, cfg: config.Config | None = None) -> str:
    """The ready-to-paste beancount block (transaction + balance assertions)."""
    cfg = cfg or config.get()
    lines = [
        f'{s.settle_date} * "{s.narration}" ^{s.link}',
        f"    due: {s.due}",
        _posting(cfg.payable_vat, -s.net),
        _posting(cfg.output_vat, s.output_vat),
    ]
    if s.bezugsteuer:
        lines.append(_posting(cfg.bezugsteuer, s.bezugsteuer))
    lines += [
        _posting(cfg.input_vat, -s.input_vat),
        "",
        f"{s.assert_date} balance {cfg.payable_vat:<38}{s.payable_after:>8.2f} CHF",
        f"{s.assert_date} balance {cfg.output_vat:<38}    0.00 CHF",
    ]
    if s.bezugsteuer:
        lines.append(f"{s.assert_date} balance {cfg.bezugsteuer:<38}    0.00 CHF")
    lines.append(f"{s.assert_date} balance {cfg.input_vat:<38}    0.00 CHF")
    return "\n".join(lines)


# ── outstanding liabilities ───────────────────────────────────────────────────


@dataclass
class Liability:
    period: str  # the VAT-<period> link
    owed: Decimal
    due: str | None
    days_left: int | None


def _as_date(v):
    if isinstance(v, Date):
        return v
    try:
        return Date.fromisoformat(str(v))
    except ValueError:
        return None


def outstanding(ledger_path: Path, today: Date | None = None,
                cfg: config.Config | None = None, entries=None):
    """Return (liabilities, unlinked_owed, total_owed, today).

    Groups PayableVAT movements by their ``^VAT-*`` link; a link nets to zero once
    its payment lands. Anything owed without such a link is reported separately.
    Pass ``entries`` to reuse an already-loaded ledger (e.g. from Fava).
    """
    cfg = cfg or config.get()
    if today is None:
        today = datetime.now(timezone.utc).date()
    if entries is None:
        entries, _ = ledger.load_entries(ledger_path)

    groups: dict[str, list] = {}  # link -> [net, due]
    unlinked = Decimal("0")
    for e in entries:
        if not isinstance(e, data.Transaction):
            continue
        link = next((l for l in (e.links or ()) if l.startswith("VAT-")), None)
        due = e.meta.get("due") if e.meta else None
        for p in e.postings:
            if p.account != cfg.payable_vat:
                continue
            if link:
                g = groups.setdefault(link, [Decimal("0"), None])
                g[0] += p.units.number
                if due is not None:
                    g[1] = due
            else:
                unlinked += p.units.number

    liabilities = []
    for link, (net, due) in groups.items():
        if net == 0:
            continue  # fully paid
        due_date = _as_date(due)
        liabilities.append(
            Liability(
                period=link,
                owed=-net,
                due=str(due_date) if due_date else None,
                days_left=(due_date - today).days if due_date else None,
            )
        )
    liabilities.sort(key=lambda l: l.due or "9999-12-31")
    total = sum((l.owed for l in liabilities), Decimal("0")) + (-unlinked)
    return liabilities, -unlinked, total, today


# ── render ────────────────────────────────────────────────────────────────────


def render_settlement(s: Settlement, console: Console | None = None) -> None:
    console = console or ui.console
    console.print()
    console.rule(f"[bold]Settlement[/] {s.link}  ·  paste into your ledger")
    console.print(f"[muted]net owed {ui.money(s.net)} CHF · due {s.due}[/]")
    console.print()
    console.print(settlement_text(s), markup=False, highlight=False)
    console.print()


def render_status(
    liabilities, unlinked, total, today, console: Console | None = None
) -> None:
    console = console or ui.console
    console.print()
    console.rule(f"[bold]VAT status[/]  ·  {today}")
    if not liabilities and unlinked == 0:
        console.print("[ok]Nothing outstanding — all filed VAT is paid.[/]")
        console.print()
        return

    t = Table(box=box.SIMPLE_HEAVY, pad_edge=False)
    t.add_column("Period", no_wrap=True)
    t.add_column("Owed CHF", justify="right", no_wrap=True)
    t.add_column("Due", no_wrap=True)
    t.add_column("Status", no_wrap=True)
    for l in liabilities:
        if l.days_left is None:
            status = "[muted]no due date[/]"
        elif l.days_left < 0:
            status = f"[owe]OVERDUE {-l.days_left} d[/]"
        else:
            style = "warn" if l.days_left <= 14 else "muted"
            status = f"[{style}]in {l.days_left} d[/]"
        t.add_row(l.period, ui.money(l.owed), l.due or "—", status)
    if unlinked:
        t.add_row("[muted](unlinked)[/]", ui.money(unlinked), "—", "")
    t.add_section()
    t.add_row("[bold]Total[/]", f"[owe]{ui.money(total)}[/]", "", "")
    console.print(t)
    console.print()
