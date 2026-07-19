"""Tests for the resumable, gap-aware price sync (no network — fake sources)."""

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from beanprice_bazg.bazg import SourcePrice
from quints import prices


def _days(begin: datetime, end: datetime) -> list[date]:
    out: list[date] = []
    d = begin.date()
    while d <= end.date():
        out.append(d)
        d += timedelta(days=1)
    return out


def _utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


class BazgLikeSource:
    """Series source with the bazg per-day progress extension.

    Publishes weekdays only — weekend requests echo the prior Friday, like
    the real BAZG endpoint.
    """

    def __init__(self) -> None:
        self.series_calls: list[tuple[str, date, date]] = []
        self.days_fetched: list[date] = []

    def get_prices_series(
        self,
        ccy: str,
        begin: datetime,
        end: datetime,
        progress: Callable[[date], None] | None = None,
    ) -> list[SourcePrice]:
        self.series_calls.append((ccy, begin.date(), end.date()))
        out: dict[date, SourcePrice] = {}
        for d in _days(begin, end):
            self.days_fetched.append(d)
            rate_day = d - timedelta(days=max(0, d.isoweekday() - 5))  # Sat/Sun -> Friday
            out[rate_day] = SourcePrice(Decimal("0.9"), _utc(rate_day), "CHF")
            if progress is not None:
                progress(d)
        return [out[k] for k in sorted(out)]


class HistoricalOnlySource:
    """Official beanprice shape: only get_historical_price, weekdays only."""

    def __init__(self, unavailable: set[date] | None = None) -> None:
        self.days_fetched: list[date] = []
        self.tickers: set[str] = set()
        self.unavailable = unavailable or set()

    def get_historical_price(self, ccy: str, time: datetime) -> SourcePrice | None:
        d = time.date()
        self.days_fetched.append(d)
        self.tickers.add(ccy)
        if d in self.unavailable:
            return None
        rate_day = d - timedelta(days=max(0, d.isoweekday() - 5))
        return SourcePrice(Decimal("1.1"), _utc(rate_day), "CHF")


class DyingSource(HistoricalOnlySource):
    """Fails with a network-ish error after N fetches — for resumability."""

    def __init__(self, survive: int) -> None:
        super().__init__()
        self.survive = survive

    def get_historical_price(self, ccy: str, time: datetime) -> SourcePrice | None:
        if len(self.days_fetched) >= self.survive:
            raise ConnectionError("boom")
        return super().get_historical_price(ccy, time)


def _eur_dates(path: Path) -> list[str]:
    return [ln.split()[0] for ln in path.read_text().splitlines() if "price EUR" in ln]


def test_initial_backfill_then_noop(tmp_path: Path) -> None:
    out = tmp_path / "prices.bean"
    out.write_text(";; header\n")
    # 2026-01-01 (Thu) .. 2026-01-07 (Wed): 7 calendar days, 5 business days.
    r1 = prices.sync(
        out,
        today=date(2026, 1, 7),
        backfill_start=date(2026, 1, 1),
        currencies=("EUR",),
        source=BazgLikeSource(),
    )
    assert r1.per_currency["EUR"].added == 5 and r1.wrote
    # Second run, same day: weekends are recorded as verified — no calls at all.
    src2 = BazgLikeSource()
    r2 = prices.sync(
        out,
        today=date(2026, 1, 7),
        backfill_start=date(2026, 1, 1),
        currencies=("EUR",),
        source=src2,
    )
    assert r2.added == 0 and not r2.wrote and src2.series_calls == []


def test_routine_sync_heals_interior_gaps(tmp_path: Path) -> None:
    out = tmp_path / "prices.bean"
    # Present: Jan 1 (Thu) and Jan 6-7; missing business days Jan 2 and Jan 5.
    out.write_text(
        ";; header\n\n2026-01-01 price EUR 0.9 CHF\n"
        "2026-01-06 price EUR 0.9 CHF\n2026-01-07 price EUR 0.9 CHF\n"
    )
    src = BazgLikeSource()
    r = prices.sync(
        out,
        today=date(2026, 1, 8),
        backfill_start=date(2026, 1, 1),
        currencies=("EUR",),
        source=src,
    )
    # Healed Jan 2 + Jan 5 (interior), added Jan 8 (forward).
    assert r.per_currency["EUR"].added == 3
    assert r.per_currency["EUR"].healed == 2
    assert "2026-01-02" in _eur_dates(out) and "2026-01-05" in _eur_dates(out)
    dates = _eur_dates(out)
    assert dates == sorted(dates)


def test_verified_watermark_prevents_refetch(tmp_path: Path) -> None:
    out = tmp_path / "prices.bean"
    out.write_text(";; header\n")
    prices.sync(
        out,
        today=date(2026, 1, 12),
        backfill_start=date(2026, 1, 1),
        currencies=("EUR",),
        source=BazgLikeSource(),
    )
    text = out.read_text()
    assert "; quints: verified EUR/CHF 2026-01-01..2026-01-12" in text
    # A later routine run fetches ONLY the new days — never the old weekends.
    src2 = BazgLikeSource()
    prices.sync(
        out,
        today=date(2026, 1, 14),
        backfill_start=date(2026, 1, 1),
        currencies=("EUR",),
        source=src2,
    )
    assert src2.days_fetched == [date(2026, 1, 13), date(2026, 1, 14)]
    assert "; quints: verified EUR/CHF 2026-01-01..2026-01-14" in out.read_text()


def test_repair_rechecks_verified_days(tmp_path: Path) -> None:
    out = tmp_path / "prices.bean"
    out.write_text(
        "; quints: verified EUR/CHF 2026-01-01..2026-01-07\n\n2026-01-01 price EUR 0.9 CHF\n"
    )
    src = BazgLikeSource()
    r = prices.sync(
        out, repair_from=date(2026, 1, 1), today=date(2026, 1, 7), currencies=("EUR",), source=src
    )
    # Every non-present day is re-fetched despite the watermark.
    assert src.days_fetched == [date(2026, 1, d) for d in (2, 3, 4, 5, 6, 7)]
    assert r.per_currency["EUR"].added == 4  # Jan 2, 5, 6, 7 (weekend echoes collapse)


def test_historical_only_source_syncs_per_day(tmp_path: Path) -> None:
    out = tmp_path / "prices.bean"
    out.write_text(";; header\n")
    src = HistoricalOnlySource()
    seen: list[tuple[str, int, int]] = []
    r = prices.sync(
        out,
        today=date(2026, 1, 7),
        backfill_start=date(2026, 1, 1),
        currencies=("EUR",),
        source=src,
        progress=lambda ccy, done, total: seen.append((ccy, done, total)),
    )
    assert r.per_currency["EUR"].added == 5  # business days
    assert src.days_fetched == [date(2026, 1, d) for d in range(1, 8)]
    # Per-day progress: announced at 0, then one tick per day.
    assert seen == [("EUR", n, 7) for n in range(8)]


def test_unavailable_days_are_retried_next_sync(tmp_path: Path) -> None:
    out = tmp_path / "prices.bean"
    out.write_text(";; header\n")
    bad = date(2026, 1, 5)  # a Monday — no weekend echo can sneak its rate in
    src = HistoricalOnlySource(unavailable={bad})
    r = prices.sync(
        out,
        today=date(2026, 1, 5),
        backfill_start=date(2026, 1, 1),
        currencies=("EUR",),
        source=src,
    )
    assert r.per_currency["EUR"].unavailable == 1
    # The failed day is NOT verified — the next sync tries it again.
    src2 = HistoricalOnlySource()
    prices.sync(
        out,
        today=date(2026, 1, 5),
        backfill_start=date(2026, 1, 1),
        currencies=("EUR",),
        source=src2,
    )
    assert bad in src2.days_fetched


def test_interrupted_sync_keeps_progress_and_resumes(tmp_path: Path) -> None:
    out = tmp_path / "prices.bean"
    out.write_text(";; header\n")
    src = DyingSource(survive=3)
    with pytest.raises(ConnectionError):
        prices.sync(
            out,
            today=date(2026, 1, 7),
            backfill_start=date(2026, 1, 1),
            currencies=("EUR",),
            source=src,
        )
    # The three fetched days were written before the crash.
    assert _eur_dates(out) == ["2026-01-01", "2026-01-02"]  # Jan 3 is a Saturday echo
    assert "; quints: verified EUR/CHF 2026-01-01..2026-01-03" in out.read_text()
    # Resume: only the unfetched tail is requested.
    src2 = HistoricalOnlySource()
    r = prices.sync(
        out,
        today=date(2026, 1, 7),
        backfill_start=date(2026, 1, 1),
        currencies=("EUR",),
        source=src2,
    )
    assert src2.days_fetched == [date(2026, 1, d) for d in (4, 5, 6, 7)]
    assert r.per_currency["EUR"].added == 3  # Mon 5, Tue 6, Wed 7


def test_series_progress_announced_per_day(tmp_path: Path) -> None:
    out = tmp_path / "prices.bean"
    out.write_text(";; header\n")
    seen: list[tuple[str, int, int]] = []
    prices.sync(
        out,
        today=date(2026, 1, 3),
        backfill_start=date(2026, 1, 1),
        currencies=("EUR", "USD"),
        source=BazgLikeSource(),
        progress=lambda ccy, done, total: seen.append((ccy, done, total)),
    )
    # Announced at 0, then one tick per fetched day, for each currency in turn.
    assert seen == [(ccy, n, 3) for ccy in ("EUR", "USD") for n in range(4)]


def test_long_backfill_writes_after_every_window(tmp_path: Path) -> None:
    out = tmp_path / "prices.bean"
    out.write_text(";; header\n")
    src = BazgLikeSource()
    prices.sync(
        out,
        today=date(2026, 3, 11),  # 70 days from Jan 1 — three 30-day windows
        backfill_start=date(2026, 1, 1),
        currencies=("EUR",),
        source=src,
    )
    assert [(b, e) for _, b, e in src.series_calls] == [
        (date(2026, 1, 1), date(2026, 1, 30)),
        (date(2026, 1, 31), date(2026, 3, 1)),
        (date(2026, 3, 2), date(2026, 3, 11)),
    ]


def test_header_and_foreign_quote_lines_preserved(tmp_path: Path) -> None:
    out = tmp_path / "prices.bean"
    out.write_text(
        ";; keep me\n;; and me\n\n2026-01-01 price EUR 0.9 CHF\n2026-01-01 price BTC 60000.00 USD\n"
    )
    prices.sync(
        out,
        today=date(2026, 1, 3),
        backfill_start=date(2026, 1, 1),
        currencies=("EUR",),
        source=BazgLikeSource(),
    )
    text = out.read_text()
    assert text.startswith(";; keep me\n;; and me")
    assert "2026-01-01 price BTC 60000.00 USD" in text


def test_ticker_mapping_translates_source_symbols(tmp_path: Path) -> None:
    out = tmp_path / "prices.bean"
    out.write_text(";; header\n")
    src = HistoricalOnlySource()
    prices.sync(
        out,
        today=date(2026, 1, 2),
        backfill_start=date(2026, 1, 1),
        currencies=("EUR",),
        tickers={"EUR": "EUR-CHF"},
        source=src,
    )
    # The source was asked for its own symbol; the file keeps the commodity.
    assert src.tickers == {"EUR-CHF"}
    assert "2026-01-01 price EUR 1.1 CHF" in out.read_text()


def test_wrong_quote_currency_fails_loudly(tmp_path: Path) -> None:
    out = tmp_path / "prices.bean"
    out.write_text(";; header\n")

    class UsdQuoted:
        def get_historical_price(self, ccy: str, time: datetime) -> SourcePrice:
            return SourcePrice(Decimal("1"), time, "USD")

    with pytest.raises(ValueError, match="quotes EUR in USD"):
        prices.sync(
            out,
            today=date(2026, 1, 2),
            backfill_start=date(2026, 1, 1),
            currencies=("EUR",),
            source=UsdQuoted(),
        )


def test_parse_source_map_matches_beanprice_syntax() -> None:
    parsed = prices.parse_source_map("USD:google/NASDAQ:AAPL,yahoo/AAPL CHF:xx/^USDCHF=X")
    assert parsed == {
        "USD": [
            prices.SourceSpec("google", "NASDAQ:AAPL"),
            prices.SourceSpec("yahoo", "AAPL"),
        ],
        "CHF": [prices.SourceSpec("xx", "USDCHF=X", invert=True)],
    }
    with pytest.raises(ValueError, match="invalid price source"):
        prices.parse_source_map("not a spec")


def test_jobs_from_ledger_reads_commodity_metadata() -> None:
    from beancount import loader

    entries, _errors, _opts = loader.load_string(
        '2024-01-01 commodity EUR\n  price: "CHF:fake_mod/EUR"\n'
        '2024-01-01 commodity BTC\n  price: ""\n'  # explicit opt-out
        "2024-01-01 commodity CHF\n"  # no metadata — ignored
        '2024-01-01 commodity GBP\n  price: "nonsense"\n'  # bad spec — reported
    )
    fake = HistoricalOnlySource()
    skipped: list[str] = []
    jobs = prices.jobs_from_ledger(
        entries,
        on_error=lambda ccy, _msg: skipped.append(ccy),
        resolver=lambda module: fake if module == "fake_mod" else None,  # type: ignore[arg-type,return-value]
    )
    assert [(j.commodity, j.quote) for j in jobs] == [("EUR", "CHF")]
    assert jobs[0].chain == (prices.ChainLink(fake, "EUR"),)
    assert skipped == ["GBP"]


def test_fallback_chain_second_source_answers(tmp_path: Path) -> None:
    out = tmp_path / "prices.bean"
    out.write_text(";; header\n")
    dead = HistoricalOnlySource(unavailable={date(2026, 1, d) for d in range(1, 8)})
    alive = HistoricalOnlySource()
    job = prices.SyncJob(
        "EUR", "CHF", (prices.ChainLink(dead, "EUR"), prices.ChainLink(alive, "EUR"))
    )
    r = prices.sync(out, today=date(2026, 1, 2), backfill_start=date(2026, 1, 1), jobs=[job])
    # The first source was consulted first, every day; the second one answered.
    assert dead.days_fetched == alive.days_fetched == [date(2026, 1, 1), date(2026, 1, 2)]
    assert r.per_currency["EUR"].added == 2


def test_inverted_source_writes_reciprocal_rate(tmp_path: Path) -> None:
    out = tmp_path / "prices.bean"
    out.write_text(";; header\n")

    class ChfUsd:  # quotes 1 CHF = 1.25 USD; ^ must store 1 USD = 0.8 CHF
        def get_historical_price(self, ccy: str, time: datetime) -> SourcePrice:
            assert ccy == "CHFUSD"
            return SourcePrice(Decimal("1.25"), time, "USD")

    job = prices.SyncJob("USD", "CHF", (prices.ChainLink(ChfUsd(), "CHFUSD", invert=True),))
    prices.sync(out, today=date(2026, 1, 1), backfill_start=date(2026, 1, 1), jobs=[job])
    assert "2026-01-01 price USD 0.8 CHF" in out.read_text()


def test_multiple_quotes_per_commodity_stay_separate(tmp_path: Path) -> None:
    out = tmp_path / "prices.bean"
    out.write_text(";; header\n")

    class Quoted:
        def __init__(self, price: str, quote: str) -> None:
            self.price, self.quote = Decimal(price), quote

        def get_historical_price(self, ccy: str, time: datetime) -> SourcePrice:
            return SourcePrice(self.price, time, self.quote)

    jobs = [
        prices.SyncJob("EUR", "CHF", (prices.ChainLink(Quoted("0.9", "CHF"), "EUR"),)),
        prices.SyncJob("EUR", "USD", (prices.ChainLink(Quoted("1.1", "USD"), "EUR"),)),
    ]
    r = prices.sync(out, today=date(2026, 1, 1), backfill_start=date(2026, 1, 1), jobs=jobs)
    # Result keys disambiguate by quote; the file keeps both series and both
    # watermarks apart.
    assert set(r.per_currency) == {"EUR/CHF", "EUR/USD"}
    text = out.read_text()
    assert "2026-01-01 price EUR 0.9 CHF" in text and "2026-01-01 price EUR 1.1 USD" in text
    assert "; quints: verified EUR/CHF 2026-01-01..2026-01-01" in text
    assert "; quints: verified EUR/USD 2026-01-01..2026-01-01" in text
