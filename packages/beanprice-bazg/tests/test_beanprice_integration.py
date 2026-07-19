"""beanprice-bazg driven by the real bean-price machinery (HTTP faked).

The README promises ``bean-price`` interop: the module resolves by name in a
source string, commodity ``price:`` metadata is understood, and the fetch
pipeline produces a beancount Price directive from our Source. These tests
pin that against the genuine beanprice package; they skip when the
``beanprice`` extra is not installed (the package itself stays standalone).
"""

import datetime as dt
from decimal import Decimal

import pytest

from beanprice_bazg import bazg

price_mod = pytest.importorskip("beanprice.price", reason="needs the beanprice extra")

DAILY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<wechselkurse xmlns="https://www.backend-rates.bazg.admin.ch/xmldaily">
  <datum>03.06.2026</datum>
  <devise code="usd">
    <waehrung>1 USD</waehrung>
    <kurs>0.79588</kurs>
  </devise>
</wechselkurse>
"""


def test_bean_price_resolves_the_module_by_name():
    ps = price_mod.parse_single_source("beanprice_bazg/USD")
    assert ps.module.Source is bazg.Source
    assert ps.symbol == "USD" and ps.invert is False


def test_bean_price_reads_commodity_metadata():
    from beancount import loader

    entries, errors, _opts = loader.load_string(
        '2024-01-01 commodity USD\n  price: "CHF:beanprice_bazg/USD"\n'
    )
    assert not errors
    declared = price_mod.find_currencies_declared(entries)
    assert [(base, quote) for base, quote, _sources in declared] == [("USD", "CHF")]
    (psource,) = declared[0][2]
    assert psource.module.Source is bazg.Source and psource.symbol == "USD"


def test_bean_price_fetch_pipeline_end_to_end(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(bazg, "_http_get", lambda url, params: DAILY_XML)
    ps = price_mod.parse_single_source("beanprice_bazg/USD")
    job = price_mod.DatedPrice("USD", "CHF", dt.date(2026, 6, 3), [ps])
    entry = price_mod.fetch_price(job)
    assert entry is not None
    assert entry.currency == "USD"
    assert entry.amount.number == Decimal("0.79588")
    assert entry.amount.currency == "CHF"
    # bean-price converts the source's UTC timestamp to the local timezone
    # before taking the date — assert through the same conversion so the test
    # holds in any timezone.
    from dateutil import tz

    src_time = dt.datetime(2026, 6, 3, tzinfo=dt.timezone.utc)
    assert entry.date == src_time.astimezone(tz.tzlocal()).date()
