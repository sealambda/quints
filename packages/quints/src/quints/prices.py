"""Sync BAZG daily CHF rates into the price file (full precision, gap-aware).

Two modes:
  * routine (no ``repair_from``): for each currency, extend forward from the
    newest date already in the file to today — fast, the daily case.
  * repair (``repair_from`` set): re-scan ``[repair_from, today]`` and add every
    missing day, healing interior gaps (e.g. a currency missing a past stretch).

Either way the file is rewritten sorted, and dates already present are never
duplicated. Existing lines (incl. any manual overrides) are preserved; only the
comment header above the first price directive and the price lines are managed.

Unlike ``bean-price --update main.bean`` — which rounds through the ledger's
``display_precision "CHF:0.01"`` — this writes the raw 5-decimal BAZG rate.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date as Date
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from beanprice_bazg.bazg import Source

from . import ledger

ProgressFn = Callable[[str, int, int], None]
"""(currency, days_fetched, days_total) — called once up front and per day."""


class PricePoint(Protocol):
    """One day's rate — the shape of ``beanprice`` ``SourcePrice``."""

    @property
    def price(self) -> Decimal: ...
    @property
    def time(self) -> datetime | None: ...


class SeriesSource(Protocol):
    """The slice of ``beanprice_bazg.Source`` that :func:`sync` needs."""

    def get_prices_series(
        self,
        ticker: str,
        time_begin: datetime,
        time_end: datetime,
        /,
        *,
        progress: Callable[[Date], None] | None = None,
    ) -> Sequence[PricePoint]: ...


DEFAULT_BACKFILL_START = Date(2024, 1, 1)
_PRICE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2}) price (\w+)\s+(\S+) CHF\s*$")


@dataclass
class SyncResult:
    per_currency: dict[str, tuple[int, Date | None]] = field(
        default_factory=dict
    )  # ccy -> (added, last_date)
    added: int = 0
    out: Path = ledger.DEFAULT_PRICES
    wrote: bool = False


def _utc(d: Date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def _ticker(progress: ProgressFn | None, ccy: str, total: int) -> Callable[[Date], None] | None:
    """Adapt the (ccy, done, total) callback to the source's per-day one."""
    if progress is None:
        return None
    progress(ccy, 0, total)  # announce the currency before the first request
    done = 0

    def tick(_day: Date) -> None:
        nonlocal done
        done += 1
        progress(ccy, done, total)

    return tick


def _read(out: Path) -> tuple[list[str], dict[tuple[str, Date], str]]:
    """Return (header_lines, {(ccy, date): price_str}) from an existing file."""
    header: list[str] = []
    entries: dict[tuple[str, Date], str] = {}
    if out.exists():
        seen_price = False
        for line in out.read_text().splitlines():
            m = _PRICE_RE.match(line)
            if m:
                seen_price = True
                y, mo, d, ccy, price = m.groups()
                entries[(ccy, Date(int(y), int(mo), int(d)))] = price
            elif not seen_price:
                header.append(line)
    return header, entries


def _write(
    out: Path,
    header: list[str],
    entries: dict[tuple[str, Date], str],
    currencies: Sequence[str],
) -> None:
    lines = list(header)
    while lines and not lines[-1].strip():  # trim trailing blank header lines
        lines.pop()
    order = list(currencies) + sorted({c for c, _ in entries} - set(currencies))
    for ccy in order:
        rows = sorted((d, p) for (c, d), p in entries.items() if c == ccy)
        if not rows:
            continue
        lines.append("")
        lines.extend(f"{d:%Y-%m-%d} price {ccy:<3} {p} CHF" for d, p in rows)
    out.write_text("\n".join(lines) + "\n")


def sync(
    out: Path,
    repair_from: Date | None = None,
    today: Date | None = None,
    currencies: Sequence[str] = ledger.PRICE_CURRENCIES,
    backfill_start: Date = DEFAULT_BACKFILL_START,
    source: SeriesSource | None = None,
    progress: ProgressFn | None = None,
) -> SyncResult:
    if today is None:
        today = datetime.now(timezone.utc).date()
    src = source or Source()
    header, entries = _read(out)

    result = SyncResult(out=out)
    for ccy in currencies:
        present = {d for (c, d) in entries if c == ccy}
        last = max(present) if present else None
        if repair_from is not None:
            start = repair_from
        elif last is not None:
            start = last + timedelta(days=1)
        else:
            start = backfill_start

        added = 0
        if start <= today:
            tick = _ticker(progress, ccy, total=(today - start).days + 1)
            for sp in src.get_prices_series(ccy, _utc(start), _utc(today), progress=tick):
                if sp.time is None:  # dateless quote — cannot be placed in the file
                    continue
                d = sp.time.date()
                if (ccy, d) in entries:  # skip present (dedups weekend echoes + repair)
                    continue
                entries[(ccy, d)] = str(sp.price)
                added += 1
        result.per_currency[ccy] = (added, last)
        result.added += added

    if result.added:
        _write(out, header, entries, currencies)
        result.wrote = True
    return result
