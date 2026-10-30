"""Cross-check a rendered invoice against its ledger transaction."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from beancount.core import data
from beancount.core import prices as bc_prices
from beancount.core.convert import get_weight

from .. import config, ledger
from .model import Invoice, Totals, vat_included


@dataclass
class Received:
    """What a pro forma's receipts booked, in the invoice currency: the legs
    the invoice that credits it releases."""

    net: Decimal = Decimal("0")  # the advance legs
    vat: Decimal = Decimal("0")  # the OutputVAT legs, VAT declared on receipt
    # The advance legs at the CHF they were booked at (kmu's book value);
    # None when a leg has no rate to value it.
    net_chf: Decimal | None = Decimal("0")
    accounts: set[str] = field(default_factory=set)

    @property
    def gross(self) -> Decimal:
        return self.net + self.vat


@dataclass
class CrossCheck:
    found: bool
    date: str | None = None
    ledger_total: Decimal | None = None
    invoice_total: Decimal | None = None
    ok: bool = False
    date_ok: bool = True  # booking date equals the invoice issue date
    # Loader errors: an entry the ledger failed to load may be the booking itself.
    violations: list[ledger.Violation] = field(default_factory=list)
    # Credited advances the ledger disagrees with: the VAT they release would
    # not net against the VAT their receipt declared.
    advance_problems: list[str] = field(default_factory=list)
    # Each credited pro forma's receipts, for the draft to release.
    received: dict[str, Received] = field(default_factory=dict)


def _tagged(e: data.Transaction, key: str, number: str) -> bool:
    """Booked under `number`: `key:` metadata, or a `^number` link."""
    return number == (e.meta or {}).get(key) or number in (e.links or set())


def _booked(txns: list[data.Transaction], currency: str, cfg: config.Config) -> Decimal | None:
    """What the transactions owe the customer, net of tax: −(turnover legs).

    The turnover legs are income, output VAT and customer advances — an
    invoice books them against its receivable, a pro forma's payment against
    the bank. None when no leg is in `currency`."""
    total = None
    for e in txns:
        for p in e.postings:
            # The weight: a CHF OutputVAT leg on a EUR invoice is `@@`-priced in EUR.
            weight = get_weight(p)
            if weight.currency != currency or weight.number is None:
                continue
            if (
                p.account.startswith(cfg.income_prefix)
                or p.account == cfg.output_vat
                or p.account in cfg.advances
            ):
                total = (total or Decimal("0")) - weight.number
    return total


def _received(
    txns: list[data.Transaction],
    currency: str,
    cfg: config.Config,
    price_map: bc_prices.PriceMap,
) -> Received | None:
    """The receipts' advance and VAT legs in `currency`; None when there are none."""
    r = None
    for e in txns:
        for p in e.postings:
            weight = get_weight(p)
            if weight.currency != currency or weight.number is None:
                continue
            if p.account == cfg.output_vat:
                r = r or Received()
                r.vat -= weight.number
            elif p.account in cfg.advances:
                r = r or Received()
                r.net -= weight.number
                r.accounts.add(p.account)
                if r.net_chf is not None:
                    try:
                        r.net_chf -= ledger.to_chf(weight, e.date, price_map)
                    except ledger.RateUnavailable:
                        r.net_chf = None
    if r is not None and r.net_chf is not None:
        r.net_chf = ledger.rappen(r.net_chf)
    return r


def _advances(
    txns: list[data.Transaction],
    inv: Invoice,
    totals: Totals,
    cfg: config.Config,
    price_map: bc_prices.PriceMap,
    tol: Decimal,
) -> tuple[dict[str, Received], list[str]]:
    """Each credited pro forma's receipts, and where they disagree with the invoice.

    Instalments of one pro forma are summed on both sides. shortcut: a pro
    forma credited across two invoices reads as over-credited on each; split
    its receipts by invoice if that ever happens."""
    credited: dict[str, Decimal] = {}
    for a in inv.advances:
        credited[a.proforma] = credited.get(a.proforma, Decimal("0")) + a.amount
    account = cfg.advances_for(inv.kind)
    received: dict[str, Received] = {}
    problems: list[str] = []
    for number, amount in credited.items():
        r = _received(
            [e for e in txns if _tagged(e, "proforma", number)], inv.currency, cfg, price_map
        )
        if r is None:
            problems.append(f"advance {number} is not booked in {inv.currency}")
            continue
        received[number] = r
        if abs(r.gross - amount) > tol:
            problems.append(
                f"advance {number} is booked at {r.gross:.2f}, the invoice credits {amount:.2f}"
            )
        if r.accounts != {account}:
            problems.append(
                f"advance {number} is booked on {', '.join(sorted(r.accounts))}, "
                f"a {inv.kind} invoice releases {account}"
            )
        split = vat_included(r.gross, totals.vat_rate)
        if abs(r.vat - split) > tol:
            problems.append(
                f"advance {number} declared {r.vat:.2f} VAT on receipt, "
                f"{split:.2f} at the invoice's {totals.vat_rate}%"
            )
        if r.net_chf is None:
            problems.append(f"advance {number} has no CHF rate for the day it was received")
    return received, problems


def cross_check(
    ledger_path: Path, inv: Invoice, totals: Totals, tol: Decimal = Decimal("0.005")
) -> CrossCheck:
    """Match by invoice id (metadata `invoice:` or a `^<number>` link) — a pro
    forma by `proforma:` — and compare the amount due to the turnover legs
    across ALL matching transactions (the payment-clearing entry may carry the
    same link — its lack of turnover legs makes it contribute zero).

    The amount due is what the invoice books against its receivable — its
    total less the advances it credits — or, for a pro forma, the payment its
    receipt booked: any part of it, as a customer may pay in instalments.
    Each credited advance is checked against its receipts."""
    cfg = config.get()
    entries, problems = ledger.load(ledger_path)
    txns = [e for e in entries if isinstance(e, data.Transaction)]
    key = "proforma" if inv.proforma else "invoice"
    matches = [e for e in txns if _tagged(e, key, inv.number)]
    price_map = bc_prices.build_price_map(entries)
    received, advance_problems = _advances(txns, inv, totals, cfg, price_map, tol)
    if not matches:
        return CrossCheck(
            found=False, violations=problems, advance_problems=advance_problems, received=received
        )

    expected = _booked(matches, inv.currency, cfg)
    # The transaction that carries the turnover legs.
    booking = next((e for e in matches if _booked([e], inv.currency, cfg) is not None), matches[0])
    expected = expected or Decimal("0")
    if inv.proforma:
        ok = Decimal("0") < expected <= totals.amount_due + tol
    else:
        ok = abs(expected - totals.amount_due) <= tol
    return CrossCheck(
        found=True,
        date=str(booking.date),
        ledger_total=expected,
        invoice_total=totals.amount_due,
        ok=ok,
        # A pro forma is booked the day it is paid, not the day it is dated.
        date_ok=inv.proforma or booking.date == inv.issue_date,
        violations=problems,
        advance_problems=advance_problems,
        received=received,
    )
