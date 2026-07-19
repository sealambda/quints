"""quints against the real beanprice package — stay a good beancount citizen.

beanprice is a dev-only dependency: these tests pin the ecosystem contract
(module resolution, Source shape, SourcePrice fields) against the genuine
article so `prices sync` keeps working with any beanprice source, not just
our own beanprice-bazg. No network — sources are instantiated, not queried.
"""

from __future__ import annotations

import inspect
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import beanprice.source
import beanprice.sources.coincap
import pytest

import beanprice_bazg
from quints import prices


def test_resolve_source_finds_bundled_beanprice_modules() -> None:
    src = prices.resolve_source("coincap")
    assert isinstance(src, beanprice.sources.coincap.Source)


def test_resolve_source_finds_plain_modules() -> None:
    src = prices.resolve_source("beanprice_bazg")
    assert isinstance(src, beanprice_bazg.Source)


def test_resolve_source_rejects_unknown_module() -> None:
    with pytest.raises(ValueError, match="cannot import price source"):
        prices.resolve_source("definitely_not_a_price_source")


def test_bazg_source_matches_beanprice_signatures() -> None:
    """beanprice calls sources positionally — parameter order is the contract."""
    for name in ("get_latest_price", "get_historical_price", "get_prices_series"):
        theirs = inspect.signature(getattr(beanprice.source.Source, name))
        ours = inspect.signature(getattr(beanprice_bazg.Source, name))
        n = len(theirs.parameters)
        assert list(ours.parameters)[:n] == list(theirs.parameters)


class _HistoricalOnly(beanprice.source.Source):
    """The common real-world shape: subclasses the beanprice base and only
    overrides get_historical_price — the inherited get_prices_series returns
    None, which sync must treat as "no series support", not as data.
    """

    def __init__(self) -> None:
        self.fetched: list[date] = []

    def get_historical_price(
        self, ticker: str, time: datetime
    ) -> beanprice.source.SourcePrice | None:
        self.fetched.append(time.date())
        return beanprice.source.SourcePrice(Decimal("2"), time, "CHF")


def test_sync_drives_a_real_beanprice_source_subclass(tmp_path: Path) -> None:
    out = tmp_path / "prices.bean"
    out.write_text(";; header\n")
    src = _HistoricalOnly()
    r = prices.sync(
        out,
        today=date(2026, 1, 3),
        backfill_start=date(2026, 1, 1),
        currencies=("EUR",),
        source=src,
    )
    # Fell back to day-by-day get_historical_price and placed real SourcePrice
    # values (the genuine beanprice NamedTuple) into the file.
    assert src.fetched == [date(2026, 1, d) for d in (1, 2, 3)]
    assert r.per_currency["EUR"].added == 3
    assert "2026-01-02 price EUR 2 CHF" in out.read_text()


def test_source_map_parsing_agrees_with_beanprice() -> None:
    """Same spec string, same interpretation as beanprice.price.parse_source_map."""
    import beanprice.price

    spec = "USD:coincap/BTC,coinbase/BTC-USD CHF:beanprice_bazg/^CHFUSD"
    ours = prices.parse_source_map(spec)
    theirs = beanprice.price.parse_source_map(spec)
    assert set(ours) == set(theirs)
    for quote, our_chain in ours.items():
        for our_spec, their_spec in zip(our_chain, theirs[quote], strict=True):
            assert our_spec.symbol == their_spec.symbol
            assert our_spec.invert == their_spec.invert
            # Our module string resolves to the same module bean-price imported.
            assert isinstance(prices.resolve_source(our_spec.module), their_spec.module.Source)
