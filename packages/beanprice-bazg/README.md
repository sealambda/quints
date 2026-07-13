# beanprice-bazg

A [beanprice](https://github.com/beancount/beanprice) price source for the
**official Swiss BAZG/EZV daily exchange rates** (Bundesamt für Zoll und
Grenzsicherheit, formerly Eidgenössische Zollverwaltung).

These are the rates the Swiss tax administration (ESTV) accepts for converting
foreign-currency amounts on VAT returns, quoted **directly in CHF** — which is
exactly what a CHF-reporting company needs, and what the ECB feed does not
provide.

## Install

This package is developed inside the [quints](https://github.com/sealambda/quints)
monorepo and isn't published to PyPI separately (only `quints` is). Use it from
a clone of the repo, or vendor `src/beanprice_bazg/` into your own project.

## Usage

Tickers are ISO currency codes; the quote currency is always `CHF`
("1 `<ticker>` = X CHF"):

```beancount
2024-01-01 commodity USD
    price: "CHF:beanprice_bazg/USD"

2024-01-01 commodity EUR
    price: "CHF:beanprice_bazg/EUR"
```

```bash
bean-price -e CHF:beanprice_bazg/USD          # latest
bean-price --update --inactive main.bean      # backfill / refresh the series
```

## Behaviour

- **Daily, fully historical.** `get_historical_price` / `get_prices_series` hit
  `…/api/xmldaily?d=YYYYMMDD`, which serves any past date.
- **Weekends & holidays** resolve to the last published rate; series results are
  de-duplicated by the actual rate date, giving a clean business-day series.
- **Per-100 quotes** (e.g. `100 EGP`) are normalised to a per-unit CHF price.
- **Monthly averages are intentionally not supported**: the BAZG monthly-average
  endpoint only ever returns the *current* month, so it cannot be backfilled by
  an automated tool. Use the daily rate consistently instead.

## Develop / test

```bash
uv sync
uv run pytest                 # offline unit tests (HTTP mocked)
BAZG_LIVE=1 uv run pytest     # also runs the live smoke test
```

## License

GPL-2.0-only
