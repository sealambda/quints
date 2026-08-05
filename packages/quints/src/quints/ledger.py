"""Shared ledger helpers — loading, price lookup, rounding, account constants."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as Date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from beancount import loader
from beancount.core import data, prices

DEFAULT_LEDGER = Path("main.bean")
DEFAULT_PRICES = Path("prices.bean")

# Entity-specific account names live in quints.toml (see quints.config).
# Swiss standard VAT rate by validity start (Art. 25 MWSTG), newest first.
# Rates are law, not configuration: changes are announced years ahead and must
# not silently corrupt historical reports — always look up by date.
VAT_RATES = (
    (Date(2024, 1, 1), Decimal("0.081")),
    (Date(2018, 1, 1), Decimal("0.077")),
    (Date(2011, 1, 1), Decimal("0.080")),
)


def vat_rate(on: Date) -> Decimal:
    """Swiss standard VAT rate in force on ``on`` (Art. 25 MWSTG)."""
    for start, r in VAT_RATES:
        if on >= start:
            return r
    raise ValueError(f"no Swiss VAT rate known for {on}")


def rappen(value: Decimal) -> Decimal:
    """Round to 0.01 CHF, half-up (Swiss Rappen rounding)."""
    return value.quantize(Decimal("0.01"), ROUND_HALF_UP)


def load_entries(ledger: Path):
    """Load a ledger; returns (entries, errors)."""
    entries, errors, _ = loader.load_file(str(ledger))
    return entries, errors


def build_price_map(ledger: Path):
    """Load a ledger and build its price map; returns (price_map, errors)."""
    entries, errors = load_entries(ledger)
    return prices.build_price_map(entries), errors


def rate(
    price_map: prices.PriceMap, ccy: str, on: Date, quote: str = "CHF"
) -> tuple[Date | None, Decimal | None]:
    """(rate_date, Decimal) for 1 <ccy> = X <quote> on/before ``on``.

    Returns (on, 1) when ccy == quote and (None, None) when no rate is
    available.
    """
    if ccy.upper() == quote.upper():
        return on, Decimal("1")
    return prices.get_price(price_map, (ccy.upper(), quote.upper()), on)


@dataclass
class Stats:
    """What a loaded ledger contains — the summary `quints check` prints."""

    directives: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    first_transaction: Date | None = None
    last_transaction: Date | None = None
    open_accounts: int = 0
    currencies: tuple[str, ...] = ()

    @property
    def transactions(self) -> int:
        return self.by_type.get("Transaction", 0)


def stats(entries: data.Directives) -> Stats:
    """Count directives by type, the transaction span, open accounts, currencies."""
    by_type: dict[str, int] = {}
    first: Date | None = None
    last: Date | None = None
    opened: set[str] = set()
    closed: set[str] = set()
    currencies: set[str] = set()
    for e in entries:
        kind = type(e).__name__
        by_type[kind] = by_type.get(kind, 0) + 1
        if isinstance(e, data.Transaction):
            first = e.date if first is None else min(first, e.date)
            last = e.date if last is None else max(last, e.date)
            for p in e.postings:
                if p.units is not None:
                    currencies.add(p.units.currency)
        elif isinstance(e, data.Open):
            opened.add(e.account)
        elif isinstance(e, data.Close):
            closed.add(e.account)
    return Stats(
        directives=len(entries),
        by_type=by_type,
        first_transaction=first,
        last_transaction=last,
        open_accounts=len(opened - closed),
        currencies=tuple(sorted(currencies)),
    )
