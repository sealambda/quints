"""Shared ledger helpers — loading, price lookup, rounding, account constants."""

from __future__ import annotations

from datetime import date as Date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from beancount import loader
from beancount.core import prices

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
PRICE_CURRENCIES = ("USD", "EUR")  # foreign currencies we price against CHF


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


def rate(price_map: prices.PriceMap, ccy: str, on: Date) -> tuple[Date | None, Decimal | None]:
    """(rate_date, Decimal) for 1 <ccy> = X CHF on/before ``on``.

    Returns (on, 1) for CHF and (None, None) when no rate is available.
    """
    if ccy.upper() == "CHF":
        return on, Decimal("1")
    return prices.get_price(price_map, (ccy.upper(), "CHF"), on)
