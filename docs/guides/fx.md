# FX rates

Swiss tax accounting uses the official BAZG/EZV daily rates, not market
rates. quints fetches them into `prices.bean` at full precision.

## Keep rates current

<!-- no-test: needs network -->
```bash
quints prices sync                     # fill gaps and extend to today
quints prices sync --from 2026-01-01   # repair mode: re-check every day
```

Needs network. Every run extends each currency forward to today **and**
fills interior gaps — days missing anywhere in the file's history are
fetched too. Days the source has no rate for (weekends, holidays) are
checked once and recorded in a `; quints: verified …` comment in the file
header, so they are never re-fetched. The file is written as rates arrive:
interrupt a long backfill and the next run resumes where it left off.
`--from` ignores the verified record and re-checks the whole range.

The BAZG API has no bulk endpoint — the fetch is one request per calendar
day per currency, so a first backfill takes a while; a live per-currency
progress bar shows how far along it is. Rates already in the file are never
touched — manual overrides survive a sync.

Which currencies to fetch, and from which source, comes from the ledger —
the same commodity `price:` metadata `bean-price` reads, with the same
syntax, so quints and the standard beancount tooling always agree on where
rates come from:

```beancount
2026-01-01 commodity EUR
  price: "CHF:beanprice_bazg/EUR"
```

Any [beanprice](https://github.com/beancount/beanprice) source works —
bundled ones by short name (`ecbrates/EUR-CHF`), third-party ones by module
path — whether it serves bulk series or single days; quints drives it
day-by-day when it has to. The full bean-price syntax is supported: fallback
chains (`"CHF:beanprice_bazg/EUR,ecbrates/EUR-CHF"` — the first source that
answers wins), inverted pairs (`^` prefix stores the reciprocal rate),
several quote currencies separated by spaces, and `price: ""` to opt a
commodity out. quints additionally refuses rates quoted in the wrong
currency, so a wrong ticker fails loudly instead of corrupting the file.

When the ledger declares no priced commodities (or you run outside a
ledger), a `[prices]` section in `quints.toml` supplies the plan instead —
and `--source MODULE` forces a specific module either way:

```toml
[prices]
source = "ecbrates"             # ECB reference rates, "BASE-SYMBOL" tickers
currencies = ["EUR"]
tickers = { EUR = "EUR-CHF" }   # 1 EUR = x CHF
```

## Why `quints prices sync` and not `bean-price`?

Both read the same commodity metadata and drive the same sources —
`bean-price` works fine against a quints ledger, and quints' own
`beanprice-bazg` source is tested against it. The difference is what happens
around the fetch:

- **The file is maintained, not just appended to.** `bean-price` prints
  directives for you to paste; sync owns `prices.bean` — sorted, one entry
  per day, weekend echoes de-duplicated, manual overrides left alone.
- **Gaps heal themselves.** `bean-price` fetches the dates you ask for.
  Every sync run re-checks the whole history and fills any missing day, so
  a stretch lost to a laptop shutdown or a source outage doesn't stay
  missing silently. Days the source genuinely has no rate for (weekends,
  holidays) are recorded in the file header and checked exactly once.
- **Interrupts are cheap.** Rates are written as they arrive; killing a
  2-year backfill loses at most one window, and the next run resumes.
- **Full precision.** `bean-price --update` rounds through the ledger's
  `display_precision`; sync writes the raw source rate (BAZG publishes five
  decimals), which is what the ESTV-accepted daily rate actually is.
- **Tax rates are daily rates.** Swiss VAT wants the official rate of the
  transaction day, not the latest quote — so the unit of work here is "keep
  a complete daily series current", which is a loop and a file format, not
  a one-shot fetch.

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
