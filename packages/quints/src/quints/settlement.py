"""VAT settlement (period close) and outstanding-liability tracking.

A settlement crystallizes a period's accrued VAT into the PayableVAT liability:
it debits OutputVAT (every rate row, 302–343) and Bezugsteuer (382/383) and
credits InputVAT (479), leaving the net (500) owed to the ESTV — or, when input
VAT ran ahead, a credit (510) that stands as a receivable from the ESTV until
it is refunded or offset. Payment follows later — Swiss VAT is due 60 days after
period end (Art. 86 MWSTG). Settlement and its eventual payment share a
``^VAT-<period>`` link, and the settlement carries a ``due:`` date, so
outstanding liabilities can be listed until paid.

Like `vat convert`, this only *prints* the transaction to paste — it never writes the
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
    method: str = "effective"
    # Saldosteuersatz only: statutory VAT invoiced minus the SSS owed. It is
    # the entity's compensation for not deducting input tax, so it lands in
    # income rather than being paid over.
    difference: Decimal = Decimal("0")


def _payable_balance(entries: data.Directives, upto: Date, cfg: config.Config) -> Decimal:
    bal = Decimal("0")
    for e in entries:
        if isinstance(e, data.Transaction) and e.date <= upto:
            for p in e.postings:
                if (
                    p.account == cfg.payable_vat
                    and p.units is not None
                    and p.units.number is not None
                ):
                    bal += p.units.number
    return bal


def build_settlement(
    ledger_path: Path,
    report: MwstReport,
    label: str | None = None,
    cfg: config.Config | None = None,
) -> Settlement:
    # The period ends where liability does: a final return after deregistering
    # mid-period is settled on its last day and due 60 days from there
    # (Art. 71 Abs. 2 MWSTG), not from the calendar period's end.
    d1 = Date.fromisoformat(report.liable_to or report.date_to)
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
        # The OutputVAT *account* balance: every rate row of the effective
        # method, net of the credit notes that reversed it (Ziffer 235) — and,
        # under SSS, the statutory VAT invoiced rather than the SSS owed.
        output_vat=report.output_vat,
        bezugsteuer=report.bezugsteuer_tax,
        input_vat=report.z479,
        net=report.z500,
        payable_after=payable_before - report.z500,
        method=report.vat_method,
        difference=(
            report.output_vat - sum((r.tax for r in report.rate_rows), Decimal("0"))
            if report.vat_method == "saldo"
            else Decimal("0")
        ),
    )


def _posting(account: str, amount: Decimal) -> str:
    return f"    {account:<38}{amount:>10.2f} CHF"


def settlement_text(s: Settlement, cfg: config.Config | None = None) -> str:
    """The ready-to-paste beancount block (transaction + balance assertions).

    Under the Saldosteuersatz method the block also empties the statutory VAT
    the invoices charged: the ESTV gets ``net`` (the SSS on the gross Entgelt
    plus Bezugsteuer) and what the customers paid on top stays in the books as
    income. There is no InputVAT leg — under SSS there is nothing to deduct.
    """
    cfg = cfg or config.get()
    saldo = s.method == "saldo"
    lines = [
        f'{s.settle_date} * "{s.narration}" ^{s.link}',
        f"    due: {s.due}",
        _posting(cfg.payable_vat, -s.net),
        _posting(cfg.output_vat, s.output_vat),
    ]
    if s.bezugsteuer:
        lines.append(_posting(cfg.bezugsteuer, s.bezugsteuer))
    if saldo:
        if s.difference:
            lines.append(_posting(cfg.saldo_difference, -s.difference))
    else:
        lines.append(_posting(cfg.input_vat, -s.input_vat))
    lines += [
        "",
        f"{s.assert_date} balance {cfg.payable_vat:<38}{s.payable_after:>8.2f} CHF",
        f"{s.assert_date} balance {cfg.output_vat:<38}    0.00 CHF",
    ]
    if s.bezugsteuer:
        lines.append(f"{s.assert_date} balance {cfg.bezugsteuer:<38}    0.00 CHF")
    if not saldo:
        lines.append(f"{s.assert_date} balance {cfg.input_vat:<38}    0.00 CHF")
    return "\n".join(lines)


# ── outstanding liabilities ───────────────────────────────────────────────────


@dataclass
class Liability:
    period: str  # the VAT-<period> link
    owed: Decimal
    due: str | None
    days_left: int | None


def _as_date(v: object) -> Date | None:
    if isinstance(v, Date):
        return v
    try:
        return Date.fromisoformat(str(v))
    except ValueError:
        return None


def outstanding(
    ledger_path: Path,
    today: Date | None = None,
    cfg: config.Config | None = None,
    entries: data.Directives | None = None,
) -> tuple[list[Liability], Decimal, Decimal, Date]:
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

    nets: dict[str, Decimal] = {}  # link -> net PayableVAT movement
    dues: dict[str, object] = {}  # link -> due date (raw metadata value)
    unlinked = Decimal("0")
    for e in entries:
        if not isinstance(e, data.Transaction):
            continue
        link = next((lk for lk in (e.links or ()) if lk.startswith("VAT-")), None)
        due = e.meta.get("due") if e.meta else None
        for p in e.postings:
            if p.account != cfg.payable_vat:
                continue
            number = p.units.number if p.units is not None else None
            if number is None:
                continue
            if link:
                nets[link] = nets.get(link, Decimal("0")) + number
                if due is not None:
                    dues[link] = due
            else:
                unlinked += number

    liabilities: list[Liability] = []
    for link, net in nets.items():
        if net == 0:
            continue  # fully paid
        due_date = _as_date(dues.get(link))
        liabilities.append(
            Liability(
                period=link,
                owed=-net,
                due=str(due_date) if due_date else None,
                days_left=(due_date - today).days if due_date else None,
            )
        )
    liabilities.sort(key=lambda liab: liab.due or "9999-12-31")
    total = sum((liab.owed for liab in liabilities), Decimal("0")) + (-unlinked)
    return liabilities, -unlinked, total, today


# ── render ────────────────────────────────────────────────────────────────────


def render_settlement(s: Settlement, console: Console | None = None) -> None:
    console = console or ui.console
    console.print()
    console.rule(f"[bold]Settlement[/] {s.link}  ·  paste into your ledger")
    # A negative net is Ziffer 510: input VAT ran ahead, so the block debits
    # PayableVAT and the balance stands as a claim on the ESTV.
    headline = (
        f"net owed {ui.money(s.net)} CHF · due {s.due}"
        if s.net >= 0
        else f"credit {ui.money(-s.net)} CHF from the ESTV (Ziffer 510)"
    )
    console.print(f"[muted]{headline}[/]")
    console.print()
    console.print(settlement_text(s), markup=False, highlight=False)
    console.print()


_PERIOD_WORDS = {"quarter": "quarterly", "half-year": "half-yearly", "year": "annually"}


def render_status(
    liabilities: list[Liability],
    unlinked: Decimal,
    total: Decimal,
    today: Date,
    console: Console | None = None,
    period_kind: str = "",
) -> None:
    console = console or ui.console
    console.print()
    cadence = _PERIOD_WORDS.get(period_kind, "")
    console.rule(f"[bold]VAT status[/]  ·  {today}" + (f"  ·  filed {cadence}" if cadence else ""))
    if not liabilities and unlinked == 0:
        console.print("[ok]Nothing outstanding — all filed VAT is paid.[/]")
        console.print()
        return

    t = Table(box=box.SIMPLE_HEAVY, pad_edge=False)
    t.add_column("Period", no_wrap=True)
    t.add_column("Owed CHF", justify="right", no_wrap=True)  # negative = credit
    t.add_column("Due", no_wrap=True)
    t.add_column("Status", no_wrap=True)
    for liab in liabilities:
        if liab.owed < 0:  # Ziffer 510 — a claim on the ESTV, nothing to pay
            status = "[refund]credit[/]"
        elif liab.days_left is None:
            status = "[muted]no due date[/]"
        elif liab.days_left < 0:
            status = f"[owe]OVERDUE {-liab.days_left} d[/]"
        else:
            style = "warn" if liab.days_left <= 14 else "muted"
            status = f"[{style}]in {liab.days_left} d[/]"
        t.add_row(liab.period, ui.money(liab.owed), liab.due or "—", status)
    if unlinked:
        t.add_row("[muted](unlinked)[/]", ui.money(unlinked), "—", "")
    t.add_section()
    style = "owe" if total >= 0 else "refund"
    t.add_row("[bold]Total[/]", f"[{style}]{ui.money(total)}[/]", "", "")
    console.print(t)
    console.print()
