"""Foreign-currency input-VAT → CHF posting, using the BAZG daily rate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from decimal import Decimal

from . import config, ledger


class RateUnavailable(Exception):
    """No <ccy>→CHF rate on or before the requested date."""

    def __init__(self, ccy: str, on: Date):
        self.ccy = ccy
        self.on = on
        super().__init__(f"no {ccy}→CHF rate on or before {on}")


@dataclass
class VatPosting:
    chf: Decimal
    rate: Decimal
    rate_date: Date | None
    foreign_vat: Decimal
    currency: str
    note: str
    input_account: str = ""
    bezugsteuer_account: str = ""

    def render(self) -> str:
        """The two lines you paste into the ledger (comment + posting)."""
        return (
            f"    ; {self.note}\n"
            f"    {self.input_account:<38} {self.chf:>8} CHF"
        )

    def render_bezugsteuer(self) -> str:
        """Reverse-charge posting pair (Bezugsteuer, Art. 45 ff. MWSTG).

        Self-assessed 8.1% on a foreign supplier's net invoice: debit InputVAT
        (deduction, Ziffer 400), credit Bezugsteuer (declaration, Ziffer 382).
        Both carry the foreign VAT as an ``@@`` price so the pair balances
        against the invoice currency.
        """
        price = "" if self.currency == "CHF" else f" @@ {self.foreign_vat} {self.currency}"
        return (
            f"    ; Bezugsteuer Art. 45 MWSTG: {self.note}\n"
            f"    {self.input_account:<38} {self.chf:>8} CHF{price}\n"
            f"    {self.bezugsteuer_account:<38} {-self.chf:>8} CHF{price}"
        )


def convert(
    amount: Decimal, currency: str, on: Date, price_map, net: bool = False,
    cfg: config.Config | None = None,
) -> VatPosting:
    """Convert a foreign VAT amount (or net price with ``net``) to a CHF posting.

    Raises :class:`RateUnavailable` if the price DB has no rate for that date.
    """
    ccy = currency.upper()
    rate_date, r = ledger.rate(price_map, ccy, on)
    if r is None:
        raise RateUnavailable(ccy, on)

    foreign_vat = ledger.rappen(amount * ledger.vat_rate(on)) if net else amount
    chf = ledger.rappen(foreign_vat * Decimal(r))
    src = f"BAZG {rate_date:%Y-%m-%d}" if rate_date else "?"

    if net:
        note = (
            f"{amount} {ccy} net → {foreign_vat} {ccy} VAT "
            f"@ {Decimal(r):.5f} CHF/{ccy} ({src})"
        )
    else:
        note = f"{foreign_vat} {ccy} @ {Decimal(r):.5f} CHF/{ccy} ({src})"

    cfg = cfg or config.get()
    return VatPosting(chf, Decimal(r), rate_date, foreign_vat, ccy, note,
                      input_account=cfg.input_vat, bezugsteuer_account=cfg.bezugsteuer)
