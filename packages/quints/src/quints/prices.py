"""Sync daily FX rates into the price file from any beanprice source.

bean-price compatible: which rates to fetch is declared the way ``bean-price``
declares it — ``price`` metadata on ``commodity`` directives, parsed with the
same source-map syntax (``CHF:beanprice_bazg/EUR``, fallback chains separated
by commas, ``^`` for inverted rates, several quote currencies separated by
spaces, ``price: ""`` to opt a commodity out)::

    2024-01-01 commodity EUR
      price: "CHF:beanprice_bazg/EUR"

When the ledger declares no priced commodities (or there is no ledger), the
``[prices]`` section of quints.toml supplies module + currencies instead.

Resumable and gap-aware:

  * every sync scans ``[backfill_start, today]`` (extended to the oldest date
    already in the file) and fetches every day that is neither present nor
    already verified — so interior gaps heal themselves, not just the forward
    edge.
  * days that were fetched but yielded no new rate (weekends, holidays) are
    recorded as *verified* in a managed header comment
    (``; quints: verified EUR/CHF 2024-01-01..2026-07-18``) so they are
    checked against the source exactly once, not on every sync.
  * the file is rewritten after every fetched window (and after every day when
    the source is driven day-by-day), so an interrupted sync keeps its
    progress and simply resumes on the next run.
  * ``repair_from`` forces a re-check of ``[repair_from, today]`` even for
    verified days (e.g. after a source bug).

Sources are beanprice source modules (``beanprice_bazg``, ``yahoo``, any
module exposing a ``Source`` class per the beanprice contract). Sources that
implement ``get_prices_series`` are called once per window; the rest are
driven day-by-day through ``get_historical_price`` — the method every
beanprice source implements — which also yields per-day progress and per-day
resumable writes. Like bean-price, a fallback chain is consulted in order and
the first source that answers wins.

The file is managed: the comment header above the first price directive and
all ``YYYY-MM-DD price CCY N QUOTE`` lines are preserved (any quote currency,
incl. manual overrides); other lines between price directives are not. Prices
are written at full source precision — unlike ``bean-price --update``, which
rounds through the ledger's ``display_precision``.
"""

from __future__ import annotations

import importlib
import inspect
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date as Date
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from beancount.core import amount, data

DEFAULT_PRICES = Path("prices.bean")
DEFAULT_SOURCE = "beanprice_bazg"
DEFAULT_CURRENCIES = ("USD", "EUR")
DEFAULT_QUOTE = "CHF"
DEFAULT_BACKFILL_START = Date(2024, 1, 1)
_WINDOW_DAYS = 30  # max days per get_prices_series call — bounds loss on interrupt

ProgressFn = Callable[[str, int, int], None]
"""(label, days_fetched, days_total) — called once up front and as days complete."""

Range = tuple[Date, Date]  # inclusive


class PricePoint(Protocol):
    """One day's rate — the shape of ``beanprice.source.SourcePrice``."""

    @property
    def price(self) -> Decimal: ...
    @property
    def time(self) -> datetime | None: ...


class HistoricalSource(Protocol):
    """A beanprice source driven day-by-day through ``get_historical_price``."""

    def get_historical_price(self, ticker: str, time: datetime, /) -> PricePoint | None: ...


class SeriesSource(Protocol):
    """A beanprice source with bulk ``get_prices_series`` support."""

    def get_prices_series(
        self, ticker: str, time_begin: datetime, time_end: datetime, /
    ) -> Sequence[PricePoint] | None: ...


PriceSource = HistoricalSource | SeriesSource
"""A beanprice source instance — must support at least one fetch method."""


_PRICE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2}) price (\w+)\s+(\S+) (\w+)\s*$")
_VERIFIED_RE = re.compile(r"^; quints: verified (\w+)/(\w+) (.*)$")
_RANGE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})$")
# bean-price's source syntax, verbatim (beanprice.price.parse_single_source).
_SOURCE_RE = re.compile(r"([a-zA-Z]+[a-zA-Z0-9\._]+)/(\^?)([a-zA-Z0-9:=_\-\.\(\)]+)$")


def resolve_source(spec: str) -> PriceSource:
    """Instantiate the ``Source`` class of a beanprice source module.

    Mirrors bean-price's module lookup: try ``beanprice.sources.<spec>``
    first (the bundled sources, when beanprice is installed), then ``spec``
    as a plain module path — so ``beanprice_bazg``, ``yahoo`` and
    ``mypkg.mysource`` all work.
    """
    module = None
    for name in (f"beanprice.sources.{spec}", spec):
        try:
            module = importlib.import_module(name)
            break
        except ImportError:
            continue
    if module is None:
        raise ValueError(
            f"cannot import price source {spec!r} (tried beanprice.sources.{spec} and {spec})"
        )
    cls = getattr(module, "Source", None)
    if cls is None:
        raise ValueError(f"price source module {spec!r} has no Source class")
    return cls()


@dataclass(frozen=True)
class SourceSpec:
    """One parsed ``<module>/[^]<symbol>`` source, bean-price semantics."""

    module: str
    symbol: str
    invert: bool = False


def parse_source_map(spec: str) -> dict[str, list[SourceSpec]]:
    """Parse bean-price ``price`` metadata: quote currency -> source chain.

    Same syntax as ``beanprice.price.parse_source_map``:
    ``<quote>:<module>/[^]<symbol>,<fallback>/... <quote2>:...`` with source
    lists separated by spaces or semicolons. Raises ValueError when invalid.
    """
    source_map: dict[str, list[SourceSpec]] = {}
    for chunk in re.split(r"[ ;]", spec):
        m = re.match(f"({amount.CURRENCY_RE}):(.*)$", chunk)
        if not m:
            raise ValueError(f"invalid price source map: {chunk!r}")
        quote, sources = m.groups()
        chain = source_map.setdefault(quote, [])
        for one in sources.split(","):
            sm = _SOURCE_RE.match(one)
            if not sm:
                raise ValueError(f"invalid price source: {one!r}")
            module, invert, symbol = sm.groups()
            chain.append(SourceSpec(module, symbol, bool(invert)))
    return source_map


@dataclass(frozen=True)
class ChainLink:
    """One fetcher in a job's fallback chain."""

    source: PriceSource
    symbol: str
    invert: bool = False


@dataclass(frozen=True)
class SyncJob:
    """Everything needed to keep one ``commodity/quote`` series current."""

    commodity: str
    quote: str
    chain: tuple[ChainLink, ...]


def jobs_from_ledger(
    entries: Iterable[object],
    on_error: Callable[[str, str], None] | None = None,
    resolver: Callable[[str], PriceSource] = resolve_source,
) -> list[SyncJob]:
    """Build sync jobs from ``commodity`` directives with ``price`` metadata.

    Mirrors bean-price's ``find_currencies_declared``: commodities without
    the metadata are ignored, ``price: ""`` opts out explicitly, and a bad
    spec skips just that commodity (``on_error(commodity, message)`` is told).
    Source modules are instantiated once and shared across jobs.
    """
    cache: dict[str, PriceSource] = {}
    jobs: list[SyncJob] = []
    for entry in entries:
        if not isinstance(entry, data.Commodity):
            continue
        spec = entry.meta.get("price")
        if spec is None or spec == "":
            continue
        try:
            source_map = parse_source_map(str(spec))
            for quote, specs in source_map.items():
                chain = tuple(
                    ChainLink(cache.setdefault(s.module, resolver(s.module)), s.symbol, s.invert)
                    for s in specs
                )
                jobs.append(SyncJob(entry.currency, quote, chain))
        except ValueError as exc:
            if on_error is not None:
                on_error(entry.currency, str(exc))
    return jobs


@dataclass
class CurrencySync:
    added: int = 0
    healed: int = 0  # of added, days that filled interior gaps (before the pre-sync newest)
    unavailable: int = 0  # days the source returned nothing for — retried next sync
    last: Date | None = None  # newest date present before this sync


@dataclass
class SyncResult:
    per_currency: dict[str, CurrencySync] = field(default_factory=dict)
    added: int = 0
    out: Path = DEFAULT_PRICES
    wrote: bool = False


def _utc(d: Date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def _merge(ranges: Iterable[Range]) -> list[Range]:
    """Sort and coalesce ranges; adjacent days (gap of 0) merge too."""
    merged: list[Range] = []
    for lo, hi in sorted(ranges):
        if merged and lo <= merged[-1][1] + timedelta(days=1):
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def _in_ranges(d: Date, ranges: Sequence[Range]) -> bool:
    return any(lo <= d <= hi for lo, hi in ranges)


@dataclass
class _PriceFile:
    """The managed price file: header, verified watermarks, price entries."""

    out: Path
    currencies: Sequence[str] = ()  # configured order — their blocks come first
    header: list[str] = field(default_factory=list)
    verified: dict[tuple[str, str], list[Range]] = field(default_factory=dict)
    # (ccy, quote) -> merged checked ranges
    entries: dict[tuple[str, Date, str], str] = field(default_factory=dict)
    # (ccy, date, quote) -> price string, kept verbatim

    @classmethod
    def read(cls, out: Path, currencies: Sequence[str]) -> _PriceFile:
        pf = cls(out=out, currencies=currencies)
        if not out.exists():
            return pf
        seen_price = False
        for line in out.read_text().splitlines():
            m = _PRICE_RE.match(line)
            if m:
                seen_price = True
                y, mo, d, ccy, price, quote = m.groups()
                pf.entries[(ccy, Date(int(y), int(mo), int(d)), quote)] = price
                continue
            v = _VERIFIED_RE.match(line)
            if v:
                ccy, quote, spans = v.groups()
                ranges = pf.verified.setdefault((ccy, quote), [])
                for span in spans.split(","):
                    r = _RANGE_RE.match(span.strip())
                    if r:
                        ranges.append((Date.fromisoformat(r[1]), Date.fromisoformat(r[2])))
                pf.verified[(ccy, quote)] = _merge(ranges)
                continue
            if not seen_price:
                pf.header.append(line)
        return pf

    def dates(self, ccy: str, quote: str) -> set[Date]:
        return {d for (c, d, q) in self.entries if c == ccy and q == quote}

    def mark_verified(self, ccy: str, quote: str, spans: Iterable[Range]) -> None:
        key = (ccy, quote)
        self.verified[key] = _merge([*self.verified.get(key, []), *spans])

    def write(self) -> None:
        lines = list(self.header)
        while lines and not lines[-1].strip():  # trim trailing blank header lines
            lines.pop()
        for ccy, quote in sorted(self.verified):
            spans = ", ".join(
                f"{lo:%Y-%m-%d}..{hi:%Y-%m-%d}" for lo, hi in self.verified[(ccy, quote)]
            )
            lines.append(f"; quints: verified {ccy}/{quote} {spans}")
        order = list(dict.fromkeys(self.currencies)) + sorted(
            {c for c, _, _ in self.entries} - set(self.currencies)
        )
        for ccy in order:
            rows = sorted((d, q, p) for (c, d, q), p in self.entries.items() if c == ccy)
            if not rows:
                continue
            lines.append("")
            lines.extend(f"{d:%Y-%m-%d} price {ccy:<3} {p} {q}" for d, q, p in rows)
        self.out.write_text("\n".join(lines) + "\n")


def _fetch_plan(
    present: set[Date], verified: Sequence[Range], start: Date, today: Date, recheck: bool
) -> list[Range]:
    """Contiguous runs of days in [start, today] that need a fetch."""
    days = [
        d
        for i in range((today - start).days + 1)
        if (d := start + timedelta(days=i)) not in present
        and (recheck or not _in_ranges(d, verified))
    ]
    return _merge((d, d) for d in days)


def _windows(runs: Sequence[Range]) -> list[Range]:
    """Chunk runs into spans of at most _WINDOW_DAYS days."""
    out: list[Range] = []
    for lo, hi in runs:
        while lo <= hi:
            cut = min(hi, lo + timedelta(days=_WINDOW_DAYS - 1))
            out.append((lo, cut))
            lo = cut + timedelta(days=1)
    return out


SeriesFn = Callable[..., "Sequence[PricePoint] | None"]
HistFn = Callable[[str, datetime], "PricePoint | None"]


@dataclass(frozen=True)
class _Fetcher:
    """A ChainLink with its fetch methods resolved."""

    symbol: str
    invert: bool
    series: SeriesFn | None
    series_takes_progress: bool
    hist: HistFn | None


def _fetcher(link: ChainLink) -> _Fetcher:
    src = link.source
    series: SeriesFn | None = getattr(src, "get_prices_series", None)
    takes_progress = False
    if series is not None:
        try:
            takes_progress = "progress" in inspect.signature(series).parameters
        except (TypeError, ValueError):
            takes_progress = False
    hist: HistFn | None = getattr(src, "get_historical_price", None)
    if series is None and hist is None:
        raise ValueError(
            f"{type(src).__name__} is not a beanprice source: it has neither "
            "get_prices_series nor get_historical_price"
        )
    return _Fetcher(link.symbol, link.invert, series, takes_progress, hist)


def sync(
    out: Path,
    repair_from: Date | None = None,
    today: Date | None = None,
    currencies: Sequence[str] = DEFAULT_CURRENCIES,
    backfill_start: Date = DEFAULT_BACKFILL_START,
    source: PriceSource | str = DEFAULT_SOURCE,
    quote: str = DEFAULT_QUOTE,
    tickers: Mapping[str, str] | None = None,
    progress: ProgressFn | None = None,
    jobs: Sequence[SyncJob] | None = None,
) -> SyncResult:
    if today is None:
        today = datetime.now(timezone.utc).date()
    if jobs is None:  # config mode: one single-source job per currency
        src = resolve_source(source) if isinstance(source, str) else source
        jobs = [
            SyncJob(ccy, quote, (ChainLink(src, (tickers or {}).get(ccy, ccy)),))
            for ccy in currencies
        ]
    repeated = {j.commodity for j in jobs if sum(k.commodity == j.commodity for k in jobs) > 1}
    pf = _PriceFile.read(out, [j.commodity for j in jobs])

    result = SyncResult(out=out)
    for job in jobs:
        label = f"{job.commodity}/{job.quote}" if job.commodity in repeated else job.commodity
        present = pf.dates(job.commodity, job.quote)
        stat = CurrencySync(last=max(present) if present else None)
        result.per_currency[label] = stat
        verified = pf.verified.get((job.commodity, job.quote), [])
        if repair_from is not None:
            start = repair_from
        else:
            start = min([backfill_start, *present, *(lo for lo, _ in verified[:1])])
        plan = _fetch_plan(present, verified, start, today, recheck=repair_from is not None)
        if plan:
            _sync_job(pf, job, label, present, plan, stat, progress)
            result.added += stat.added
            result.wrote = True
    return result


def _sync_job(
    pf: _PriceFile,
    job: SyncJob,
    label: str,
    present: set[Date],
    plan: Sequence[Range],
    stat: CurrencySync,
    progress: ProgressFn | None,
) -> None:
    """Fetch every day in ``plan``, flushing the file as results arrive."""
    ccy, quote = job.commodity, job.quote
    # Days that already hold a rate need no fetch — fold them into the
    # verified record so it coalesces into few spans instead of one per weekend.
    pf.mark_verified(ccy, quote, ((d, d) for d in present))
    fetchers = [_fetcher(link) for link in job.chain]
    total = sum((hi - lo).days + 1 for lo, hi in plan)
    done = 0

    def tick(n: int) -> None:
        nonlocal done
        done += n
        if progress is not None:
            progress(label, done, total)

    if progress is not None:
        progress(label, 0, total)

    for lo, hi in _windows(plan):
        points: Sequence[PricePoint] | None = None
        used: _Fetcher | None = None
        for f in fetchers:  # first source in the chain that answers wins
            if f.series is None:
                continue
            kwargs: dict[str, object] = (
                {"progress": lambda _d: tick(1)} if f.series_takes_progress else {}
            )
            points = f.series(
                f.symbol, _utc(lo), _utc(hi) + timedelta(days=1, seconds=-1), **kwargs
            )
            if points is not None:
                if not f.series_takes_progress:
                    tick((hi - lo).days + 1)
                used = f
                break
        if points is not None and used is not None:  # whole window answered
            _absorb(pf, points, job, used.invert, stat)
            pf.mark_verified(ccy, quote, [(lo, hi)])
            pf.write()
            continue
        if all(f.hist is None for f in fetchers):  # series-only chain that failed
            stat.unavailable += (hi - lo).days + 1
            tick((hi - lo).days + 1)
            continue
        day = lo  # no series support (or it failed) — drive day by day
        while day <= hi:
            sp: PricePoint | None = None
            for f in fetchers:
                if f.hist is None:
                    continue
                sp = f.hist(f.symbol, _utc(day) + timedelta(hours=12))
                if sp is not None:
                    _absorb(pf, [sp], job, f.invert, stat)
                    pf.mark_verified(ccy, quote, [(day, day)])
                    break
            if sp is None:
                stat.unavailable += 1
            tick(1)
            pf.write()
            day += timedelta(days=1)


def _absorb(
    pf: _PriceFile, points: Sequence[PricePoint], job: SyncJob, invert: bool, stat: CurrencySync
) -> None:
    """Add dated points not already present (dedups weekend echoes + repair)."""
    for sp in points:
        # A wrong quote currency would silently corrupt the books — refuse it.
        # (The file already flushed for prior windows, so nothing is lost.)
        # Inverted sources are exempt: their quote is the pair's base, which
        # bean-price doesn't validate either.
        actual = getattr(sp, "quote_currency", None)
        if not invert and actual is not None and actual != job.quote:
            raise ValueError(
                f"source quotes {job.commodity} in {actual}, but the price file "
                f"needs {job.quote} — check the commodity's price metadata or "
                f"[prices] in quints.toml"
            )
        if sp.time is None:  # dateless quote — cannot be placed in the file
            continue
        d = sp.time.date()
        if (job.commodity, d, job.quote) in pf.entries:
            continue
        price = Decimal(1) / sp.price if invert else sp.price
        pf.entries[(job.commodity, d, job.quote)] = str(price)
        stat.added += 1
        if stat.last is not None and d < stat.last:
            stat.healed += 1
