"""Tests for the gap-aware price sync (no network — a fake source is injected)."""

from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from beanprice_bazg.bazg import SourcePrice
from quints import prices


class FakeSource:
    """Returns one price per calendar day in the requested range."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, date, date]] = []

    def get_prices_series(
        self,
        ccy: str,
        begin: datetime,
        end: datetime,
        progress: Callable[[date], None] | None = None,
    ) -> list[SourcePrice]:
        self.calls.append((ccy, begin.date(), end.date()))
        out: list[SourcePrice] = []
        d = begin
        while d <= end:
            out.append(SourcePrice(Decimal("0.9"), d, "CHF"))
            if progress is not None:
                progress(d.date())
            d += timedelta(days=1)
        return out


def _eur_dates(path: Path) -> list[str]:
    return [ln.split()[0] for ln in path.read_text().splitlines() if "price EUR" in ln]


def test_initial_backfill_then_noop(tmp_path: Path) -> None:
    out = tmp_path / "prices.bean"
    out.write_text(";; header\n")
    r1 = prices.sync(
        out,
        today=date(2026, 1, 5),
        backfill_start=date(2026, 1, 1),
        currencies=("EUR",),
        source=FakeSource(),
    )
    assert r1.added == 5 and r1.wrote
    # Second run, same day → nothing to add.
    r2 = prices.sync(out, today=date(2026, 1, 5), currencies=("EUR",), source=FakeSource())
    assert r2.added == 0 and not r2.wrote


def test_routine_extends_forward_only(tmp_path: Path) -> None:
    out = tmp_path / "prices.bean"
    out.write_text(";; header\n\n2026-01-01 price EUR 0.9 CHF\n2026-01-05 price EUR 0.9 CHF\n")
    # Routine sync must NOT backfill the 01-02..01-04 interior gap, only extend forward.
    r = prices.sync(out, today=date(2026, 1, 7), currencies=("EUR",), source=FakeSource())
    assert r.added == 2  # 01-06, 01-07 only
    assert "2026-01-03" not in _eur_dates(out)


def test_repair_heals_interior_gap_and_sorts(tmp_path: Path) -> None:
    out = tmp_path / "prices.bean"
    # Present: 01-01 and 01-06..01-07; missing 01-02..01-05.
    out.write_text(
        ";; header\n\n2026-01-06 price EUR 0.9 CHF\n2026-01-01 price EUR 0.9 CHF\n"
        "2026-01-07 price EUR 0.9 CHF\n"
    )
    r = prices.sync(
        out,
        repair_from=date(2026, 1, 1),
        today=date(2026, 1, 7),
        currencies=("EUR",),
        source=FakeSource(),
    )
    assert r.added == 4  # 01-02..01-05
    dates = _eur_dates(out)
    assert dates == sorted(dates)  # rewritten sorted
    assert dates.count("2026-01-06") == 1  # no duplicates


def test_progress_reports_each_day_per_currency(tmp_path: Path) -> None:
    out = tmp_path / "prices.bean"
    out.write_text(";; header\n")
    seen: list[tuple[str, int, int]] = []
    prices.sync(
        out,
        today=date(2026, 1, 3),
        backfill_start=date(2026, 1, 1),
        currencies=("EUR", "USD"),
        source=FakeSource(),
        progress=lambda ccy, done, total: seen.append((ccy, done, total)),
    )
    # Announced at 0, then one tick per fetched day, for each currency in turn.
    assert seen == [(ccy, n, 3) for ccy in ("EUR", "USD") for n in range(4)]


def test_header_preserved(tmp_path: Path) -> None:
    out = tmp_path / "prices.bean"
    out.write_text(";; keep me\n;; and me\n\n2026-01-01 price EUR 0.9 CHF\n")
    prices.sync(out, today=date(2026, 1, 3), currencies=("EUR",), source=FakeSource())
    assert out.read_text().startswith(";; keep me\n;; and me")
