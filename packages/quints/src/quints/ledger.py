"""Shared ledger helpers — loading, price lookup, rounding, account constants."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date as Date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from beancount import loader
from beancount.core import convert, data, prices
from beancount.core.amount import Amount


class InvariantError(AssertionError):
    """A state the code assumes can never happen: a quints bug, not bad books."""


def invariant(condition: bool, message: str) -> None:
    """Always-on assertion. Plain ``assert`` is stripped under ``python -O``.

    For programmer errors only. Problems in the user's books or files are
    reported (a violation, a ``ValueError``), never asserted.
    """
    if not condition:
        raise InvariantError(message)


DEFAULT_LEDGER = Path("main.bean")
DEFAULT_PRICES = Path("prices.bean")

# Entity-specific account names live in quints.toml (see quints.config).
# Swiss VAT rates by validity start (Art. 25 MWSTG), newest first. Rates are
# law, not configuration: changes are announced years ahead and must not
# silently corrupt historical reports — always look up by date.
#
#   VAT_RATES          Normalsatz            (Art. 25 Abs. 1)
#   VAT_RATES_REDUCED  reduzierter Satz      (Art. 25 Abs. 2)
#   VAT_RATES_LODGING  Sondersatz Beherbergung (Art. 25 Abs. 4)
#
# The 2024 step is the AHV-financing increase (8.1 / 2.6 / 3.8 %); 2018 lowered
# the standard and lodging rates when the IV supplement lapsed, leaving the
# reduced rate untouched.
VAT_RATES = (
    (Date(2024, 1, 1), Decimal("0.081")),
    (Date(2018, 1, 1), Decimal("0.077")),
    (Date(2011, 1, 1), Decimal("0.080")),
)
VAT_RATES_REDUCED = (
    (Date(2024, 1, 1), Decimal("0.026")),
    (Date(2011, 1, 1), Decimal("0.025")),
)
VAT_RATES_LODGING = (
    (Date(2024, 1, 1), Decimal("0.038")),
    (Date(2018, 1, 1), Decimal("0.037")),
    (Date(2011, 1, 1), Decimal("0.038")),
)

# Rate class → its date-ranged table. The keys are the vocabulary the MWST
# report and the ``mwst:`` metadata share.
VAT_RATE_CLASSES: dict[str, tuple[tuple[Date, Decimal], ...]] = {
    "standard": VAT_RATES,
    "reduced": VAT_RATES_REDUCED,
    "lodging": VAT_RATES_LODGING,
}


# Saldosteuersätze (Art. 37 MWSTG): the ESTV grants a business one or more of
# this discrete list, per branch/activity. The list itself is law — Verordnung
# der ESTV über die Höhe der Saldosteuersätze nach Branchen und Tätigkeiten,
# SR 641.202.62 (vom 5.9.2024, Stand 1.1.2025, AS 2024 500); the 2024 step came
# with the rate increase (AS 2023 18). Newest first, like VAT_RATES.
# Spelled as the ordinance prints them, in per cent.
_SSS_FROM_2024 = "0.1 0.6 1.3 2.1 3.0 3.7 4.5 5.3 6.2 6.8"
_SSS_UNTIL_2023 = "0.1 0.6 1.2 2.0 2.8 3.5 4.3 5.1 5.9 6.5"


def _percents(spec: str) -> tuple[Decimal, ...]:
    return tuple(Decimal(p) / 100 for p in spec.split())


SALDO_RATES: tuple[tuple[Date, tuple[Decimal, ...]], ...] = (
    (Date(2024, 1, 1), _percents(_SSS_FROM_2024)),
    (Date(2011, 1, 1), _percents(_SSS_UNTIL_2023)),
)


def saldo_rates(on: Date) -> tuple[Decimal, ...]:
    """The Saldosteuersätze the ESTV could grant on ``on`` (SR 641.202.62)."""
    for start, rates in SALDO_RATES:
        if on >= start:
            return rates
    raise ValueError(f"no Swiss Saldosteuersatz list known for {on}")


def is_saldo_rate(rate: Decimal) -> bool:
    """True if ``rate`` is (or was) a permitted Saldosteuersatz.

    Any vintage counts: a business still declaring a pre-2024 supply under
    Ziffer 322 does so at the rate granted back then.
    """
    return any(rate in rates for _start, rates in SALDO_RATES)


def vat_rate(on: Date, rate_class: str = "standard") -> Decimal:
    """Swiss VAT rate of ``rate_class`` in force on ``on`` (Art. 25 MWSTG)."""
    table = VAT_RATE_CLASSES.get(rate_class)
    if table is None:
        raise ValueError(f"unknown VAT rate class {rate_class!r}")
    for start, r in table:
        if on >= start:
            return r
    raise ValueError(f"no Swiss {rate_class} VAT rate known for {on}")


def rappen(value: Decimal) -> Decimal:
    """Round to 0.01 CHF, half-up (Swiss Rappen rounding)."""
    return value.quantize(Decimal("0.01"), ROUND_HALF_UP)


def load_entries(ledger: Path):
    """Load a ledger; returns (entries, errors).

    Raw beancount errors, for ``quints check``. A report loads with
    :func:`load` instead, so the errors become its violations.
    """
    entries, errors, _ = loader.load_file(str(ledger))
    return entries, errors


@dataclass
class Violation:
    """A problem in the books a report cannot represent — listed, never silently dropped."""

    date: str
    payee: str
    narration: str
    message: str
    expected: Decimal = Decimal("0")
    posted: Decimal = Decimal("0")


def violations(errors: Sequence[object], root: Path) -> list[Violation]:
    """Beancount loader errors as report violations, ``file:line`` relative to ``root``.

    The loader drops what it cannot read (a missing include, a syntax error)
    and keeps the rest, so a report computed from it is short: it must say so.
    """
    out: list[Violation] = []
    for err in errors:
        source = getattr(err, "source", None) or {}
        filename, lineno = source.get("filename"), source.get("lineno")
        where = ""
        if filename and not str(filename).startswith("<"):  # "<load>": no file to point at
            name = Path(str(filename))
            where = str(name.relative_to(root) if name.is_relative_to(root) else name)
            where += f":{lineno}" if lineno is not None else ""
        entry = getattr(err, "entry", None)
        on = getattr(entry, "date", None)
        txn = entry if isinstance(entry, data.Transaction) else None
        out.append(
            Violation(
                str(on) if on is not None else "",
                (txn.payee or "") if txn else "",
                (txn.narration or "") if txn else "",
                f"ledger error ({where}): {getattr(err, 'message', err)}"
                if where
                else f"ledger error: {getattr(err, 'message', err)}",
            )
        )
    return out


def load(ledger: Path) -> tuple[data.Directives, list[Violation]]:
    """Load a ledger for a report: (entries, its loader errors as violations)."""
    entries, errors = load_entries(ledger)
    # beancount names files by os.path.abspath — symlinks unresolved — so the
    # root must be spelled the same way for relative_to to match.
    return entries, violations(errors, Path(os.path.abspath(ledger)).parent)


class RateUnavailable(Exception):
    """No <ccy>→CHF rate on or before the requested date."""

    def __init__(self, ccy: str, on: Date):
        self.ccy = ccy
        self.on = on
        super().__init__(f"no {ccy}→CHF rate on or before {on}")


def to_chf(units: Amount, on: Date, price_map: prices.PriceMap) -> Decimal:
    """``units`` in CHF at ``on`` (the price map's latest rate on/before it).

    Raises :class:`RateUnavailable` rather than valuing the amount at 0.
    """
    number = units.number
    if number is None:  # the loader interpolates every amount
        raise InvariantError(f"incomplete amount {units} in a loaded ledger")
    if units.currency == "CHF":
        return number
    conv = convert.convert_amount(units, "CHF", price_map, date=on)
    if conv.currency != "CHF" or conv.number is None:
        raise RateUnavailable(units.currency, on)
    return conv.number


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
