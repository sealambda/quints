"""beanprice source for official Swiss BAZG/EZV daily FX rates (quoted in CHF).

BAZG (Bundesamt für Zoll und Grenzsicherheit, formerly EZV) publishes the
official Swiss customs exchange rates. These are the "Tageskurs" the ESTV
accepts for converting foreign-currency amounts on VAT returns, so they are the
natural rate source for a CHF-reporting Swiss company.

Tickers are ISO currency codes (``USD``, ``EUR``, ``GBP`` …). The quote currency
is always ``CHF`` — i.e. the price answers "1 <ticker> = X CHF".

Usage in a beancount ``commodity`` directive::

    2024-01-01 commodity USD
        price: "CHF:beanprice_bazg/USD"

    2024-01-01 commodity EUR
        price: "CHF:beanprice_bazg/EUR"

Then::

    bean-price --update main.bean          # backfill/refresh
    bean-price -e CHF:beanprice_bazg/USD   # one-off latest

API notes (discovered empirically):
  * Daily endpoint ``/api/xmldaily?d=YYYYMMDD`` serves any historical date; on
    weekends/holidays it returns the last published rate (its <datum> differs
    from the requested day), so a series is de-duplicated by the actual rate
    date.
  * The monthly-average endpoint only ever returns the *current* month and is
    therefore not fetchable for history — this source deliberately uses the
    daily rate only.
"""

from __future__ import annotations

from collections import namedtuple
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from xml.etree import ElementTree

import requests

try:  # Use beanprice's types when available, but stay importable/testable without it.
    from beanprice import source as _bp_source  # pyright: ignore[reportMissingImports]

    _Base = _bp_source.Source
    SourcePrice = _bp_source.SourcePrice
except ImportError:  # pragma: no cover - exercised only when beanprice is absent
    _Base = object
    SourcePrice = namedtuple("SourcePrice", "price time quote_currency")

__all__ = ["BAZGError", "Source", "SourcePrice"]

QUOTE_CURRENCY = "CHF"
DAILY_URL = "https://www.backend-rates.bazg.admin.ch/api/xmldaily"
_TIMEOUT = 30


class BAZGError(ValueError):
    """Raised when the BAZG API cannot satisfy a request."""


def _localname(tag: str) -> str:
    """Strip an ``{namespace}`` prefix from an ElementTree tag."""
    return tag.rsplit("}", 1)[-1]


_session: requests.Session | None = None


def _http_get(url: str, params: dict) -> str:
    """Single network seam — monkeypatched in tests.

    One shared Session: a series fetch is one request per day, and reusing the
    TLS connection cuts most of the per-request overhead."""
    global _session
    if _session is None:
        _session = requests.Session()
    resp = _session.get(url, params=params, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def _parse_daily(xml_text: str, ticker: str) -> tuple[Decimal, datetime]:
    """Return (price_in_CHF, rate_date_utc) for ``ticker`` from a daily XML doc."""
    code = ticker.strip().lower()
    root = ElementTree.fromstring(xml_text)  # noqa: S314 — official BAZG endpoint over HTTPS

    rate_date = None
    for child in root:
        if _localname(child.tag) == "datum" and child.text:
            rate_date = _parse_date(child.text.strip())
            break

    for devise in root:
        if _localname(devise.tag) != "devise":
            continue
        if (devise.get("code") or "").lower() != code:
            continue
        waehrung = kurs = None
        for field in devise:
            name = _localname(field.tag)
            if name == "waehrung":
                waehrung = (field.text or "").strip()
            elif name == "kurs":
                kurs = (field.text or "").strip()
        if not kurs:
            raise BAZGError(f"No <kurs> for {ticker!r} in BAZG response")
        multiplier = _unit_multiplier(waehrung)
        price = (Decimal(kurs) / multiplier).normalize()
        if rate_date is None:
            raise BAZGError("BAZG response missing <datum>")
        return price, rate_date

    raise BAZGError(
        f"Currency {ticker!r} not found in BAZG daily rates "
        f"(expected an ISO code like USD, EUR, GBP)"
    )


def _unit_multiplier(waehrung: str | None) -> Decimal:
    """``waehrung`` is e.g. '1 USD' or '100 EGP'; return the leading count."""
    if not waehrung:
        return Decimal(1)
    head = waehrung.split()[0]
    try:
        return Decimal(head)
    except Exception:
        return Decimal(1)


def _parse_date(text: str) -> datetime:
    """Parse BAZG's 'dd.mm.yyyy' into a UTC-aware datetime at midnight."""
    day, month, year = (int(p) for p in text.split("."))
    return datetime(year, month, day, tzinfo=timezone.utc)


class Source(_Base):
    """beanprice source for BAZG daily CHF rates."""

    def get_latest_price(self, ticker: str) -> SourcePrice:
        # No ``d`` param → current published rate.
        xml_text = _http_get(DAILY_URL, {"locale": "en"})
        price, rate_date = _parse_daily(xml_text, ticker)
        return SourcePrice(price, rate_date, QUOTE_CURRENCY)

    def get_historical_price(self, ticker: str, time: datetime) -> SourcePrice:
        xml_text = _http_get(DAILY_URL, {"d": time.strftime("%Y%m%d"), "locale": "en"})
        price, rate_date = _parse_daily(xml_text, ticker)
        return SourcePrice(price, rate_date, QUOTE_CURRENCY)

    def get_prices_series(
        self,
        ticker: str,
        time_begin: datetime,
        time_end: datetime,
        progress: Callable[[date], None] | None = None,
    ) -> list[SourcePrice]:
        """Fetch one price per day in [begin, end], de-duplicated by rate date.

        BAZG has no bulk endpoint, so this issues one request per calendar day.
        Weekend/holiday days resolve to the prior business day's rate and are
        collapsed, yielding a clean business-day series. ``progress`` (if
        given) is called with each calendar day as its request completes, so
        callers can show a live bar over the slow one-request-per-day fetch.
        """
        results: dict[datetime, SourcePrice] = {}
        day = time_begin.date()
        end = time_end.date()
        while day <= end:
            xml_text = _http_get(DAILY_URL, {"d": day.strftime("%Y%m%d"), "locale": "en"})
            price, rate_date = _parse_daily(xml_text, ticker)
            results[rate_date] = SourcePrice(price, rate_date, QUOTE_CURRENCY)
            if progress is not None:
                progress(day)
            day += timedelta(days=1)
        return [results[k] for k in sorted(results)]
