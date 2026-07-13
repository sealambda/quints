"""Year-end FX revaluation of non-CHF GmbH balances (OR Art. 960 ff.).

Beancount accounts hold plain units, so a currency account's **book value in
CHF** is implicit: the sum of each posting's CHF weight (its booked ``@``/
``@@`` rate, or the BAZG rate of the transaction date) — the same valuation
the Erfolgsrechnung uses. The revaluation books, per currency, the difference
between that book value and the balance valued at the report-date BAZG rate:

    2026-12-31 * "Wise" "Year-end FX revaluation EUR (Art. 960 OR)"
        Assets:…:Wise:EUR   -6395.74 EUR @@ 5952.86 CHF   ; out at book value
        Assets:…:Wise:EUR    6395.74 EUR @@ 5900.11 CHF   ; back at year-end rate
        Expenses:CH:GmbH:FX:CurrencyLoss  52.75 CHF

Units net to zero; the CHF delta lands in CurrencyGain/CurrencyLoss. After
booking, the account's implicit book value equals the year-end market value,
so the KMU Bilanz's balancing result and the Erfolgsrechnung agree — the
unrealized FX becomes explicit instead of a reconciling difference.

Like the settlement, this prints ledger text to review and paste — it never
writes the books.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from decimal import Decimal
from pathlib import Path

from beancount.core import convert as bc_convert
from beancount.core import data
from beancount.core import prices as bc_prices
from beancount.core.inventory import Inventory
from rich.console import Console

from . import config, ledger, ui


@dataclass
class Revaluation:
    currency: str
    account: str
    units: Decimal
    book_chf: Decimal      # implicit book value (booked rates / txn-date BAZG)
    market_chf: Decimal    # units at the report-date BAZG rate
    rate: Decimal          # report-date rate used
    rate_date: Date        # BAZG date of that rate

    @property
    def delta(self) -> Decimal:
        """Positive = unrealized gain, negative = loss."""
        return self.market_chf - self.book_chf


class RateUnavailable(RuntimeError):
    def __init__(self, ccy: str, on: Date):
        super().__init__(f"no {ccy}→CHF rate on or before {on}")
        self.ccy, self.on = ccy, on


def compute(ledger_path: Path, at: str,
            cfg: config.Config | None = None) -> list[Revaluation]:
    cfg = cfg or config.get()
    on = Date.fromisoformat(at)
    entries, _ = ledger.load_entries(ledger_path)
    price_map = bc_prices.build_price_map(entries)

    inventories: dict[str, Inventory] = {}
    book: dict[tuple[str, str], Decimal] = {}  # (account, ccy) → CHF book value
    for e in entries:
        if not isinstance(e, data.Transaction) or e.date > on:
            continue
        for p in e.postings:
            root = p.account.split(":", 1)[0]
            if cfg.entity_marker not in p.account or root not in ("Assets", "Liabilities"):
                continue
            if p.units.currency == "CHF":
                continue
            inventories.setdefault(p.account, Inventory()).add_position(p)
            weight = bc_convert.get_weight(p)
            if weight.currency == "CHF":
                chf = weight.number
            else:
                conv = bc_convert.convert_amount(weight, "CHF", price_map, date=e.date)
                chf = conv.number if conv.currency == "CHF" else None
            if chf is None:
                raise RateUnavailable(p.units.currency, e.date)
            key = (p.account, p.units.currency)
            book[key] = book.get(key, Decimal("0")) + chf

    revaluations: list[Revaluation] = []
    for account, inv in sorted(inventories.items()):
        for pos in inv:
            units = pos.units.number.quantize(Decimal("0.01"))
            if not units:
                continue
            rate_date, rate = ledger.rate(price_map, pos.units.currency, on)
            if rate is None:
                raise RateUnavailable(pos.units.currency, on)
            market = ledger.rappen(units * Decimal(rate))
            book_value = ledger.rappen(book.get((account, pos.units.currency), Decimal("0")))
            revaluations.append(
                Revaluation(
                    currency=pos.units.currency,
                    account=account,
                    units=units,
                    book_chf=book_value,
                    market_chf=market,
                    rate=Decimal(rate),
                    rate_date=rate_date,
                )
            )
    return revaluations


def revaluation_text(revaluations: list[Revaluation], at: str,
                     cfg: config.Config | None = None) -> str:
    """Paste-ready ledger text, one transaction per currency."""
    cfg = cfg or config.get()
    by_ccy: dict[str, list[Revaluation]] = {}
    for r in revaluations:
        if r.delta:
            by_ccy.setdefault(r.currency, []).append(r)

    chunks: list[str] = []
    for ccy, items in sorted(by_ccy.items()):
        lines = [
            f'{at} * "Wise" "Year-end FX revaluation {ccy} (Art. 960 OR)"',
        ]
        total_delta = Decimal("0")
        for r in items:
            lines.append(
                f"    ; {r.units} {ccy} @ {r.rate:.5f} CHF/{ccy} (BAZG {r.rate_date}) "
                f"= {r.market_chf} CHF vs {r.book_chf} CHF book"
            )
            lines.append(f"    {r.account:<48} {-r.units:>12} {ccy} @@ {r.book_chf} CHF")
            lines.append(f"    {r.account:<48} {r.units:>12} {ccy} @@ {r.market_chf} CHF")
            total_delta += r.delta
        if total_delta > 0:
            lines.append(f"    {cfg.fx_gain:<48} {-total_delta:>12} CHF")
        else:
            lines.append(f"    {cfg.fx_loss:<48} {-total_delta:>12} CHF")
        chunks.append("\n".join(lines))
    return "\n\n".join(chunks)


def render(revaluations: list[Revaluation], at: str, console: Console | None = None) -> None:
    console = console or ui.console
    if not any(r.delta for r in revaluations):
        console.print(f"No unrealized FX at {at} — nothing to revalue.", style="ok")
        return
    console.print()
    console.rule(f"[bold]FX revaluation[/]   per {at}")
    for r in revaluations:
        style = "refund" if r.delta > 0 else "owe" if r.delta < 0 else "muted"
        console.print(
            f"  {r.account}  {r.units} {r.currency}: book {ui.money(r.book_chf)} → "
            f"market {ui.money(r.market_chf)} CHF  [{style}]{ui.money(r.delta)}[/]"
        )
    console.print()
    console.print("Paste into books/<year>.bean (review first):", style="muted")
    console.print()
    console.print(revaluation_text(revaluations, at), highlight=False)
    console.print()
