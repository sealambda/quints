# FX rates

Swiss tax accounting uses the official BAZG/EZV daily rates, not market
rates. quints fetches them into `prices.bean` at full precision.

## Keep rates current

<!-- no-test: needs network -->
```bash
quints prices sync                     # extend each currency forward to today
quints prices sync --from 2026-01-01   # repair mode: fill any missing days
```

Needs network. Rates already in the file are never touched — manual
overrides survive a sync. Which currencies to fetch comes from
`commodities.bean`:

```beancount
2026-01-01 commodity EUR
  price: "CHF:beanprice_bazg/EUR"
```

`bean-price` reads the same metadata, so the standard beancount tooling
agrees with quints on where rates come from.

## Year-end revaluation

```bash
quints fx revalue --at 2026-12-31
```

Prints the year-end revaluation transaction (Art. 960 OR): unrealized FX
differences on non-CHF balances, booked against the configured
`fx_gain`/`fx_loss` accounts. Review it, paste it into `books/<year>.bean`.
Like everything in quints, it's printed for review — never written into your
books directly.

For VAT amounts on foreign invoices, use [`quints vat`](vat.md#foreign-currency-vat)
instead of manual conversion.
