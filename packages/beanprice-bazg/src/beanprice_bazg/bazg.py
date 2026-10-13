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

import threading
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import NamedTuple
from xml.etree import ElementTree

import requests


class SourcePrice(NamedTuple):
    """Structurally identical to ``beanprice.source.SourcePrice``.

    Defined locally so the module stays importable/testable without beanprice;
    beanprice consumes the fields by attribute, so this is a drop-in value.
    """

    price: Decimal
    time: datetime | None
    quote_currency: str | None


__all__ = ["BAZGError", "Source", "SourcePrice"]

QUOTE_CURRENCY = "CHF"
DAILY_URL = "https://www.backend-rates.bazg.admin.ch/api/xmldaily"
_TIMEOUT = 30


class BAZGError(ValueError):
    """Raised when the BAZG API cannot satisfy a request."""


def _localname(tag: str) -> str:
    """Strip an ``{namespace}`` prefix from an ElementTree tag."""
    return tag.rsplit("}", 1)[-1]


_local = threading.local()


def _thread_session() -> requests.Session:
    """This thread's Session, created on first use.

    A series fetch is one request per day, and reusing the TLS connection cuts
    most of the per-request overhead. Per thread, not per process: bean-price
    runs ``fetch_price`` on a ``ThreadPoolExecutor`` (``-w N``) and
    ``requests.Session`` is not thread-safe. Not per ``Source`` either:
    bean-price builds a new ``Source()`` for every job, so an instance
    Session would never be reused, and ``_http_get`` must keep its
    ``(url, params)`` shape as the test seam.
    """
    session = getattr(_local, "session", None)
    if not isinstance(session, requests.Session):
        session = requests.Session()
        _local.session = session
    return session


def _http_get(url: str, params: dict[str, str]) -> str:
    """Single network seam — monkeypatched in tests."""
    resp = _thread_session().get(url, params=params, timeout=_TIMEOUT)
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
        multiplier = _unit_multiplier(waehrung, code)
        price = (Decimal(kurs) / multiplier).normalize()
        if rate_date is None:
            raise BAZGError("BAZG response missing <datum>")
        return price, rate_date

    raise BAZGError(
        f"Currency {ticker!r} not found in BAZG daily rates "
        f"(expected an ISO code like USD, EUR, GBP)"
    )


def _unit_multiplier(waehrung: str | None, code: str) -> Decimal:
    """``waehrung`` is e.g. '1 USD' or '100 EGP'; return the leading count.

    Anything else raises: a guessed 1 would store a per-100 rate 100x too high.
    """
    parts = (waehrung or "").split()
    try:
        count = Decimal(parts[0]) if len(parts) == 2 else None
    except InvalidOperation:
        count = None
    if count is None or not count.is_finite() or count <= 0 or parts[1].lower() != code:
        raise BAZGError(f"Unreadable <waehrung> {waehrung!r} for {code.upper()} in BAZG response")
    return count


def _parse_date(text: str) -> datetime:
    """Parse BAZG's 'dd.mm.yyyy' into a UTC-aware datetime at midnight."""
    day, month, year = (int(p) for p in text.split("."))
    return datetime(year, month, day, tzinfo=timezone.utc)


class Source:
    """beanprice source for BAZG daily CHF rates.

    Implements the ``beanprice.source.Source`` interface structurally;
    beanprice instantiates and calls it duck-typed, so no base class is
    needed and the module stays importable without beanprice.
    """

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
