"""VAT liability — does the entity have to be registered, and from when?

The rule is a turnover threshold (Art. 10 Abs. 2 Bst. a MWSTG): a business is
exempt while it earns less than CHF 100'000 a year, worldwide, from supplies
that are not exempt under Art. 21 — measured on the agreed Entgelte **without**
VAT (Abs. 2bis). An existing business that reaches it is liable from the end
of the business year it reached it in; a year that was not a full year of
activity is converted to a full year (Art. 9 Abs. 3 MWSTV, MWST-Info 02
Ziff. 5.3). Registration is due within 30 days of liability starting
(Art. 66 Abs. 1 MWSTG).

The other direction: a registered business that stays under the threshold may
deregister at the end of the tax period it stayed under, if it does not expect
to reach it in the next — notifying the ESTV within 60 days; not deregistering
counts as waiving the exemption (Art. 14 Abs. 5 MWSTG, MWST-Info 02 Ziff. 6.2).

What quints cannot know is the forward-looking half of the rule: a *new*
business that expects to pass the threshold within its first 12 months is
liable from day one (Art. 14 Abs. 3 MWSTG, Art. 9 Abs. 1-2 MWSTV). That is an
expectation, not a booking, so it is stated, never computed.

Turnover is read the way `quints vat report` reads it — the same `mwst:` tags,
`kmu:` codes and income markers — and counts every bucket except the exempt
supplies (Ziffer 230) and the Nicht-Entgelte (900/910). The business year is
taken to be the calendar year, like the books (one file per year).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as Date
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from beancount.core import data
from beancount.core import prices as bc_prices
from beancount.core.amount import Amount
from rich import box
from rich.console import Console
from rich.table import Table

from . import config, kmu, ledger, mwst, ui

THRESHOLD = Decimal("100000")  # Art. 10 Abs. 2 Bst. a MWSTG
REGISTER_WITHIN_DAYS = 30  # Art. 66 Abs. 1 MWSTG
DEREGISTER_WITHIN_DAYS = 60  # Art. 14 Abs. 5 MWSTG; MWST-Info 02 Ziff. 6.2
# Buckets that do not count towards the threshold: supplies exempt under
# Art. 21 without option, and Nicht-Entgelte (Art. 18 Abs. 2).
_NOT_COUNTED = frozenset({"exempt", "subvention", "donation"})


@dataclass
class YearTurnover:
    """One business year against the threshold."""

    year: int
    turnover: Decimal  # CHF, net of VAT, the counted buckets only
    months: Decimal  # months of activity in the year (12 for a full year)
    annualised: Decimal  # turnover converted to a full year
    reached: bool
    registered: bool  # VAT-registered for (part of) the year
    complete: bool = True  # False for the year still running on `at`


@dataclass
class Liability:
    activity_since: str  # the first booking — what the conversion counts from
    registered: bool  # registered on `at`, per quints.toml
    at: str = ""
    years: list[YearTurnover] = field(default_factory=list)
    status: str = ""  # below | on_course | must_register | liable | may_deregister
    liable_from: str | None = None  # when registration became mandatory
    register_by: str | None = None
    advice: list[str] = field(default_factory=list)


def _days_in_month(year: int, month: int) -> int:
    nxt = Date(year + (month == 12), month % 12 + 1, 1)
    return (nxt - Date(year, month, 1)).days


def months_active(first: Date, last: Date) -> Decimal:
    """Calendar months from ``first`` to ``last``, both days included.

    Whole months count 1, a partial month its share of days — the ESTV's own
    example converts 1 March to 31 December as exactly ten months
    (MWST-Info 02 Ziff. 5.3).
    """
    total = Decimal(0)
    cursor = Date(first.year, first.month, 1)
    while cursor <= last:
        dim = _days_in_month(cursor.year, cursor.month)
        lo = max(first, cursor)
        hi = min(last, Date(cursor.year, cursor.month, dim))
        total += Decimal((hi - lo).days + 1) / Decimal(dim)
        cursor = Date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    return total


def _turnover_by_year(
    entries: data.Directives, cfg: config.Config
) -> tuple[dict[int, Decimal], Date | None]:
    price_map = bc_prices.build_price_map(entries)
    codes = kmu.kmu_map(entries, cfg.entity_marker)
    totals: dict[int, Decimal] = {}
    first: Date | None = None
    for e in entries:
        if not isinstance(e, data.Transaction):
            continue
        first = e.date if first is None or e.date < first else first
        if any(p.account == cfg.payable_vat for p in e.postings):
            continue  # a VAT settlement, not a supply
        for p in e.postings:
            if not p.account.startswith(cfg.income_prefix) or p.units is None:
                continue
            number = p.units.number
            if number is None:
                continue
            tokens = mwst.posting_tokens(p, e)
            bucket = mwst.turnover_bucket(p.account, codes.get(p.account), tokens, cfg)
            if bucket is None or bucket in _NOT_COUNTED:
                continue
            # A credit note or rebate (Ziffer 235) is a debit, so it subtracts.
            chf = mwst.to_chf(Amount(-number, p.units.currency), e.date, price_map)
            totals[e.date.year] = totals.get(e.date.year, Decimal(0)) + chf
    return totals, first


def compute(ledger_path: Path, at: Date, cfg: config.Config | None = None) -> Liability:
    """Every business year up to the one containing ``at``, against the threshold.

    The year ``at`` falls in is converted from the months elapsed so far and
    marked incomplete: the rule looks at the business year once it has ended.
    """
    cfg = cfg or config.get()
    entries, _errors = ledger.load_entries(ledger_path)
    totals, first = _turnover_by_year(entries, cfg)
    start = min(first, at) if first else Date(at.year, 1, 1)
    result = Liability(activity_since=str(start), registered=cfg.liable_on(at), at=str(at))
    for y in range(start.year, at.year + 1):
        complete = y < at.year or at == Date(y, 12, 31)
        span_end = Date(y, 12, 31) if complete else at
        months = months_active(max(start, Date(y, 1, 1)), span_end)
        turnover = totals.get(y, Decimal(0))
        annualised = (turnover * 12 / months).quantize(Decimal("0.01"), ROUND_HALF_UP)
        registered = cfg.liable_on(Date(y, 12, 31)) or cfg.liable_on(Date(y, 1, 1))
        result.years.append(
            YearTurnover(
                y, turnover, months, annualised, annualised >= THRESHOLD, registered, complete
            )
        )
    _conclude(result)
    return result


def _conclude(result: Liability) -> None:
    last = result.years[-1]
    done = [y for y in result.years if y.complete]
    if not result.registered:
        reached = next((y for y in done if y.reached), None)
        running = None if last.complete else last
        if reached is None and running is not None and running.reached:
            result.status = "on_course"
            result.advice.append(
                f"{running.year} so far converts to {ui.money(running.annualised)} CHF for a full "
                f"year — on course to reach the threshold. If it does, you are liable from "
                f"{running.year + 1}-01-01 and must register within 30 days "
                "(Art. 9 Abs. 3 MWSTV, Art. 66 Abs. 1 MWSTG)."
            )
        elif reached is None:
            result.status = "below"
            result.advice.append(
                f"Below CHF 100'000 in every year so far ({ui.money(last.annualised)} CHF "
                f"for {last.year}, converted to a full year): no registration needed "
                "(Art. 10 Abs. 2 Bst. a MWSTG). Voluntary registration is possible "
                "(Art. 11 MWSTG)."
            )
        else:
            since = Date(reached.year + 1, 1, 1)
            result.status = "must_register"
            result.liable_from = str(since)
            result.register_by = str(since + timedelta(days=REGISTER_WITHIN_DAYS))
            result.advice.append(
                f"{reached.year} reached the threshold ({ui.money(reached.annualised)} CHF, "
                f"converted to a full year): liable from {since} (Art. 9 Abs. 3 MWSTV). "
                f"Register with the ESTV within 30 days — by {result.register_by} "
                "(Art. 66 Abs. 1 MWSTG) — then set vat_registered_since in quints.toml."
            )
        result.advice.append(
            "A new business that expects to pass CHF 100'000 within its first 12 months is "
            "liable from its start (Art. 14 Abs. 3 MWSTG, Art. 9 MWSTV) — an expectation "
            "quints cannot compute; reassess after three months at the latest."
        )
        return
    if not done:
        result.status = "liable"
        result.advice.append(
            f"{last.year} is still running ({ui.money(last.annualised)} CHF converted to a full "
            "year so far) — the threshold is judged once the business year has ended."
        )
        return
    last = done[-1]
    if last.reached:
        result.status = "liable"
        result.advice.append(
            f"{last.year}: {ui.money(last.annualised)} CHF — above the threshold; stay registered."
        )
        return
    result.status = "may_deregister"
    deadline = Date(last.year, 12, 31) + timedelta(days=DEREGISTER_WITHIN_DAYS)
    result.advice.append(
        f"{last.year}: {ui.money(last.annualised)} CHF — below the threshold. If you do not "
        f"expect to reach it in {last.year + 1}, you may deregister as of {last.year}-12-31, "
        f"notifying the ESTV by {deadline}; staying registered counts as a voluntary waiver "
        "(Art. 14 Abs. 5 MWSTG). If you do, set vat_registered_until in quints.toml."
    )


# ── render ────────────────────────────────────────────────────────────────────


def render(result: Liability, console: Console | None = None) -> None:
    console = console or ui.console
    console.print()
    console.rule("[bold]VAT liability[/]  ·  threshold CHF 100'000 (Art. 10 MWSTG)")
    console.print(
        f"activity since {result.activity_since} (first booking) · "
        + ("registered" if result.registered else "not registered"),
        style="muted",
        justify="center",
    )
    t = Table(box=box.SIMPLE_HEAVY, pad_edge=False)
    t.add_column("Year", no_wrap=True)
    t.add_column("Turnover CHF", justify="right", no_wrap=True)
    t.add_column("Months", justify="right", no_wrap=True)
    t.add_column("Full year CHF", justify="right", no_wrap=True)
    t.add_column("Threshold", no_wrap=True)
    for y in result.years:
        mark = "[owe]reached[/]" if y.reached else "[ok]below[/]"
        t.add_row(
            str(y.year) if y.complete else f"{y.year} (to {result.at})",
            ui.money(y.turnover),
            f"{y.months.quantize(Decimal('0.1'))}",
            ui.money(y.annualised),
            mark,
        )
    console.print(t)
    for line in result.advice:
        console.print(f"[warn]![/] {line}")
    console.print()
