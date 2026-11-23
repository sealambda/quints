# FX rates

Keep `prices.bean` filled with official rates, so every foreign amount
converts to CHF the way the tax authorities accept. For VAT, the ESTV
publishes a monthly average and a daily selling rate, and you keep your
choice for at least a tax period.[^fx] quints uses the daily rate, fetched
from the BAZG at full precision.

!!! abstract "Applies if"
    - **Legal form:** any.
    - **VAT status:** any. The rates matter for VAT once you're registered,
      and for the year-end valuation always.
    - **Currencies:** any the BAZG publishes, or any other
      [beanprice](https://github.com/beancount/beanprice) source.

## Keep rates current

<!-- no-test: needs network -->
```bash
quints prices sync                     # fill gaps and extend to today
quints prices sync --from 2026-01-01   # repair mode: re-check every day
```

Needs network. Each run extends every currency to today and fetches any day
missing from the file's history.

- **Days without a rate** (weekends, holidays) are checked once and recorded
  in a `; quints: verified …` comment in the file header. A day counts as
  checked only once a newer rate is in the file, so today is asked again
  until the source publishes. `--from` ignores the record and re-checks the
  whole range.
- **Ten years back at most,** the time books are kept:[^keep] a mistyped
  year in an old line can't start a century of requests.
- **Interruptible.** The file is written as rates arrive, and the next run
  resumes. Each write replaces the file in one step, so a crash leaves the
  old one.
- **Slow the first time.** The BAZG API takes one request per day per
  currency. A progress bar per currency shows how far along it is.
- **Manual overrides survive.** A rate already in the file is never
  touched, and keeps the `; comment` on its line.
- **Nothing but prices below the first price.** Another directive or a
  stray note there is refused before anything is fetched, because the
  rewrite would drop it.

### Which currencies, from which source

The ledger says, in the commodity `price:` metadata that `bean-price` reads:

```beancount
2026-01-01 commodity EUR
  price: "CHF:beanprice_bazg/EUR"
```

Any [beanprice](https://github.com/beancount/beanprice) source works:
bundled ones by short name (`ecbrates/EUR-CHF`), third-party ones by module
path. The full bean-price syntax is supported:

- **Fallback chains:** `"CHF:beanprice_bazg/EUR,ecbrates/EUR-CHF"`. The
  first source that answers wins.
- **Inverted pairs:** a `^` prefix stores the reciprocal rate.
- **Several quote currencies,** separated by spaces.
- **`price: ""`** opts a commodity out.

quints refuses a rate quoted in the wrong currency, so a wrong ticker fails
instead of corrupting the file. A `price:` value it can't parse is listed,
the other currencies still sync, and the run exits 1.

When the ledger declares no priced commodity, or you run outside a ledger,
`[prices]` in `quints.toml` supplies the plan. `--source MODULE` forces a
module either way:

```toml
[prices]
source = "ecbrates"             # ECB reference rates, "BASE-SYMBOL" tickers
currencies = ["EUR"]
tickers = { EUR = "EUR-CHF" }   # 1 EUR = x CHF
```

## Why `quints prices sync` and not `bean-price`?

Both read the same metadata and drive the same sources. `bean-price` works
against a quints ledger, and `beanprice-bazg` is tested against it. Sync
adds:

- **A maintained file.** `bean-price` prints directives for you to paste.
  Sync owns `prices.bean`: sorted, one entry per day, weekend echoes
  de-duplicated, manual overrides left alone.
- **Gap filling.** `bean-price` fetches the dates you ask for. Sync
  re-checks the whole history on every run.
- **Full precision.** `bean-price --update` rounds through the ledger's
  `display_precision`. Sync writes the raw source rate (BAZG publishes five
  decimals).
- **The rate of the day.** quints converts at the official rate of the
  transaction day (the daily option of Art. 45 MWSTV), not at the latest
  quote, so it needs a complete daily series.

## Year-end revaluation

```bash
quints fx revalue --at 2026-12-31
```

It prints the year-end revaluation transaction:[^960] unrealized FX
differences on non-CHF balances, booked against the `fx_gain`/`fx_loss`
accounts. Review it and paste it into `books/<year>.bean`.
`quints close check` says whether it is still outstanding
([Close the year](year-end.md)).

For VAT on a foreign invoice, use
[`quints vat convert`](vat.md#foreign-currency-vat).

[^fx]: Art. 45 MWSTV, [SR 641.201](https://www.fedlex.admin.ch/eli/cc/2009/828/de#art_45). Abs. 3: "nach dem von der ESTV veröffentlichten Wechselkurs, wobei wahlweise der Monatsmittelkurs oder der Tageskurs für den Verkauf von Devisen verwendet werden kann"; Abs. 5: the choice is kept "während mindestens einer Steuerperiode". Input tax follows the same rule (Art. 58 MWSTV). [MWST-Info 07 *Steuerbemessung und Steuersätze*, Ziff. 1.3.2](https://www.gate.estv.admin.ch/mwst-webpublikationen/public/pages/taxInfos/tableOfContent.xhtml?publicationId=1007607&lang=de); [ESTV, Fremdwährungskurse](https://www.estv.admin.ch/de/mwst-fremdwaehrungskurse); the daily rates: [BAZG](https://www.rates.bazg.admin.ch/home).
[^960]: Valuation: Art. 960 ff. OR; the currency of the accounts and the disclosure of the rates used: Art. 958d Abs. 3 OR. [SR 220](https://www.fedlex.admin.ch/eli/cc/27/317_321_377/de#art_958_d).
[^keep]: Art. 958f Abs. 1 OR: "Die Geschäftsbücher und die Buchungsbelege … sind während zehn Jahren aufzubewahren." [fedlex](https://www.fedlex.admin.ch/eli/cc/27/317_321_377/de#art_958_f).
