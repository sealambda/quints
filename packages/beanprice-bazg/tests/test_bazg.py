"""Unit tests for beanprice_bazg — offline by default (HTTP is monkeypatched)."""

import io
import os
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal

import pytest
import requests

from beanprice_bazg import bazg

# A trimmed but structurally faithful daily document (default namespace + a
# per-1 currency, a per-100 currency, and the <datum>/<waehrung>/<kurs> shape).
DAILY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<wechselkurse xmlns="https://www.backend-rates.bazg.admin.ch/xmldaily">
  <datum>03.06.2026</datum>
  <zeit>07:00:03</zeit>
  <gueltigkeit>04.06.2026</gueltigkeit>
  <devise code="eur">
    <land_en>Euro Member</land_en>
    <waehrung>1 EUR</waehrung>
    <kurs>0.92526</kurs>
  </devise>
  <devise code="usd">
    <land_en>United States</land_en>
    <waehrung>1 USD</waehrung>
    <kurs>0.79588</kurs>
  </devise>
  <devise code="egp">
    <land_en>Egypt</land_en>
    <waehrung>100 EGP</waehrung>
    <kurs>1.66619</kurs>
  </devise>
</wechselkurse>
"""

# Weekend request: BAZG echoes the prior business day's <datum> (Friday).
WEEKEND_XML = DAILY_XML.replace("<datum>03.06.2026</datum>", "<datum>05.06.2026</datum>")


def _fixed(xml: str) -> Callable[[str, dict[str, str]], str]:
    return lambda url, params: xml


def test_parse_per_unit_currency(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(bazg, "_http_get", _fixed(DAILY_XML))
    sp = bazg.Source().get_latest_price("USD")
    assert sp.price == Decimal("0.79588")
    assert sp.quote_currency == "CHF"
    assert sp.time == datetime(2026, 6, 3, tzinfo=timezone.utc)


def test_ticker_is_case_insensitive(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(bazg, "_http_get", _fixed(DAILY_XML))
    assert bazg.Source().get_latest_price("eur").price == Decimal("0.92526")


def test_per_100_currency_is_divided(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(bazg, "_http_get", _fixed(DAILY_XML))
    # 1.66619 CHF per 100 EGP  ->  0.0166619 CHF per EGP
    assert bazg.Source().get_latest_price("EGP").price == Decimal("0.0166619")


@pytest.mark.parametrize(
    "waehrung",
    ["<waehrung>EGP 100</waehrung>", "<waehrung>100</waehrung>", "<waehrung>0 EGP</waehrung>", ""],
)
def test_unreadable_unit_raises(monkeypatch: pytest.MonkeyPatch, waehrung: str):
    # A guessed multiplier of 1 would store the per-100 rate 100x too high.
    xml = DAILY_XML.replace("<waehrung>100 EGP</waehrung>", waehrung)
    monkeypatch.setattr(bazg, "_http_get", _fixed(xml))
    with pytest.raises(bazg.BAZGError, match="EGP"):
        bazg.Source().get_latest_price("EGP")


def test_unknown_currency_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(bazg, "_http_get", _fixed(DAILY_XML))
    with pytest.raises(bazg.BAZGError):
        bazg.Source().get_latest_price("XYZ")


def test_historical_passes_date_param(monkeypatch: pytest.MonkeyPatch):
    seen: dict[str, str] = {}

    def fake(url: str, params: dict[str, str]) -> str:
        seen.update(params)
        return DAILY_XML

    monkeypatch.setattr(bazg, "_http_get", fake)
    bazg.Source().get_historical_price("USD", datetime(2026, 6, 3))
    assert seen["d"] == "20260603"


def test_series_reports_progress_per_requested_day(monkeypatch: pytest.MonkeyPatch):
    # The callback fires per requested calendar day (the network unit of work),
    # even when weekend echoes collapse into fewer series points.
    monkeypatch.setattr(bazg, "_http_get", _fixed(WEEKEND_XML))
    seen = []
    bazg.Source().get_prices_series(
        "USD", datetime(2026, 6, 5), datetime(2026, 6, 7), progress=seen.append
    )
    assert seen == [datetime(2026, 6, d).date() for d in (5, 6, 7)]


def test_series_dedupes_by_actual_rate_date(monkeypatch: pytest.MonkeyPatch):
    # Fri 05.06 returns its own date; Sat/Sun echo Fri -> one point, not three.
    def fake(url: str, params: dict[str, str]) -> str:
        return WEEKEND_XML

    monkeypatch.setattr(bazg, "_http_get", fake)
    series = bazg.Source().get_prices_series("USD", datetime(2026, 6, 5), datetime(2026, 6, 7))
    assert len(series) == 1
    assert series[0].time == datetime(2026, 6, 5, tzinfo=timezone.utc)


@pytest.mark.skipif(
    os.environ.get("BAZG_LIVE") != "1", reason="set BAZG_LIVE=1 to hit the real API"
)
def test_live_smoke():
    sp = bazg.Source().get_historical_price("USD", datetime(2026, 6, 3))
    assert Decimal("0.5") < sp.price < Decimal("2")
    assert sp.quote_currency == "CHF"


def test_each_thread_gets_its_own_session(monkeypatch: pytest.MonkeyPatch):
    """bean-price fetches from a ThreadPoolExecutor (``-w N``); a shared
    ``requests.Session`` is not thread-safe."""
    used: list[tuple[str, requests.Session]] = []

    class Recorder(requests.Session):
        def get(self, url: str | bytes, *args: object, **kwargs: object) -> requests.Response:
            used.append((threading.current_thread().name, self))
            response = requests.Response()
            response.status_code = 200
            response.raw = io.BytesIO(DAILY_XML.encode())
            return response

    monkeypatch.setattr(bazg.requests, "Session", Recorder)

    def fetch_twice() -> None:
        bazg.Source().get_latest_price("USD")
        bazg.Source().get_latest_price("EUR")

    # Fresh threads only: their session cache starts empty, so nothing real
    # (and no network) can be picked up from an earlier test.
    workers = [threading.Thread(target=fetch_twice, name=name) for name in ("a", "b")]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    by_thread = {name: [s for n, s in used if n == name] for name in ("a", "b")}
    assert all(len(s) == 2 and s[0] is s[1] for s in by_thread.values())  # reused per thread
    assert by_thread["a"][0] is not by_thread["b"][0]  # never shared across threads
