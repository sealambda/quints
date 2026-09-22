"""Foreign-currency input-VAT → CHF posting, using the BAZG daily rate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from decimal import Decimal

from beancount.core import prices as bc_prices

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
    expense_account: str = ""  # SSS: the non-deductible Bezugsteuer cost
    rate_class: str = "standard"
    method: str = "effective"

    def render(self) -> str:
        """The two lines you paste into the ledger (comment + posting).

        Under the Saldosteuersatz method there is nothing to paste: the input
        tax is already compensated by the SSS (Art. 37 MWSTG), so the purchase
        is booked gross and the CHF figure is only there to check the invoice.
        """
        if self.method == "saldo":
            return (
                f"    ; {self.note}\n"
                "    ; Saldosteuersatz method: input VAT is not deductible — "
                "book the gross amount as the expense."
            )
        return f"    ; {self.note}\n    {self.input_account:<38} {self.chf:>8} CHF"

    def render_bezugsteuer(self) -> str:
        """Reverse-charge posting pair (Bezugsteuer, Art. 45 ff. MWSTG).

        Self-assessed at the rate in force on a foreign supplier's net invoice:
        debit InputVAT (deduction, Ziffer 400/405), credit Bezugsteuer
        (declaration, Ziffer 383 — 382 for a pre-2024 supply). Both carry the
        foreign VAT as an ``@@`` price so the pair balances against the invoice
        currency.

        Under the Saldosteuersatz method the Bezugsteuer is still owed at the
        statutory rate but cannot be deducted (Art. 37 MWSTG), so the debit is
        an expense instead: the pair costs real money rather than netting out.
        """
        price = "" if self.currency == "CHF" else f" @@ {self.foreign_vat} {self.currency}"
        # A non-standard rate has to travel with the posting, or `vat report`
        # would value the declaration at the standard rate and flag it.
        tag = "" if self.rate_class == "standard" else f'\n        mwst: "{self.rate_class}"'
        return (
            f"    ; Bezugsteuer Art. 45 MWSTG: {self.note}\n"
            f"    {self.debit_account:<38} {self.chf:>8} CHF{price}\n"
            f"    {self.bezugsteuer_account:<38} {-self.chf:>8} CHF{price}{tag}"
        )

    @property
    def debit_account(self) -> str:
        """Where the self-assessed tax lands: a deduction, or a cost under SSS."""
        return self.expense_account if self.method == "saldo" else self.input_account


def convert(
    amount: Decimal,
    currency: str,
    on: Date,
    price_map: bc_prices.PriceMap,
    net: bool = False,
    cfg: config.Config | None = None,
    rate_class: str = "standard",
) -> VatPosting:
    """Convert a foreign VAT amount (or net price with ``net``) to a CHF posting.

    ``rate_class`` picks the Art. 25 MWSTG rate applied to a ``net`` amount —
    ``standard`` (8.1 %), ``reduced`` (2.6 %) or ``lodging`` (3.8 %), always
    looked up by date. Raises :class:`RateUnavailable` if the price DB has no
    rate for that date, and ``ValueError`` for an unknown rate class.
    """
    ccy = currency.upper()
    vat = ledger.vat_rate(on, rate_class)  # validates rate_class before any I/O
    rate_date, r = ledger.rate(price_map, ccy, on)
    if r is None:
        raise RateUnavailable(ccy, on)

    foreign_vat = ledger.rappen(amount * vat) if net else amount
    chf = ledger.rappen(foreign_vat * Decimal(r))
    src = f"BAZG {rate_date:%Y-%m-%d}" if rate_date else "?"

    if net:
        note = f"{amount} {ccy} net → {foreign_vat} {ccy} VAT @ {Decimal(r):.5f} CHF/{ccy} ({src})"
    else:
        note = f"{foreign_vat} {ccy} @ {Decimal(r):.5f} CHF/{ccy} ({src})"

    cfg = cfg or config.get()
    return VatPosting(
        chf,
        Decimal(r),
        rate_date,
        foreign_vat,
        ccy,
        note,
        input_account=cfg.input_vat,
        bezugsteuer_account=cfg.bezugsteuer,
        expense_account=cfg.bezugsteuer_expense,
        rate_class=rate_class,
        method=cfg.vat_method,
    )
