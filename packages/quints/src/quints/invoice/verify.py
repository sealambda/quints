"""Cross-check a rendered invoice against its ledger transaction."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from beancount.core import data
from beancount.core.convert import get_weight

from .. import config, ledger
from .model import Invoice, Totals


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


def _tagged(e: data.Transaction, key: str, number: str) -> bool:
    """Booked under `number`: `key:` metadata, or a `^number` link."""
    return number == (e.meta or {}).get(key) or number in (e.links or set())


def _booked(txns: list[data.Transaction], currency: str, cfg: config.Config) -> Decimal | None:
    """What the transactions owe the customer, net of tax: −(turnover legs).

    The turnover legs are income, output VAT and customer advances — an
    invoice books them against its receivable, a pro forma's payment against
    the bank. None when no leg is in `currency`."""
    advances = (cfg.advances_domestic, cfg.advances_export)
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
                or p.account in advances
            ):
                total = (total or Decimal("0")) - weight.number
    return total


def cross_check(
    ledger_path: Path, inv: Invoice, totals: Totals, tol: Decimal = Decimal("0.005")
) -> CrossCheck:
    """Match by invoice id (metadata `invoice:` or a `^<number>` link) — a pro
    forma by `proforma:` — and compare the amount due to the turnover legs
    across ALL matching transactions (the payment-clearing entry may carry the
    same link — its lack of turnover legs makes it contribute zero).

    The amount due is what the invoice books against its receivable — its
    total less the advances it credits — or, for a pro forma, the payment its
    receipt booked. Each credited advance is checked against that receipt."""
    cfg = config.get()
    entries, problems = ledger.load(ledger_path)
    txns = [e for e in entries if isinstance(e, data.Transaction)]
    key = "proforma" if inv.proforma else "invoice"
    matches = [e for e in txns if _tagged(e, key, inv.number)]
    advance_problems = []
    for a in inv.advances:
        received = _booked(
            [e for e in txns if _tagged(e, "proforma", a.proforma)], inv.currency, cfg
        )
        if received is None:
            advance_problems.append(f"advance {a.proforma} is not booked in {inv.currency}")
        elif abs(received - a.amount) > tol:
            advance_problems.append(
                f"advance {a.proforma} is booked at {received:.2f}, "
                f"the invoice credits {a.amount:.2f}"
            )
    if not matches:
        return CrossCheck(found=False, violations=problems, advance_problems=advance_problems)

    expected = _booked(matches, inv.currency, cfg)
    # The transaction that carries the turnover legs.
    booking = next((e for e in matches if _booked([e], inv.currency, cfg) is not None), matches[0])
    expected = expected or Decimal("0")
    return CrossCheck(
        found=True,
        date=str(booking.date),
        ledger_total=expected,
        invoice_total=totals.amount_due,
        ok=abs(expected - totals.amount_due) <= tol,
        # A pro forma is booked the day it is paid, not the day it is dated.
        date_ok=inv.proforma or booking.date == inv.issue_date,
        violations=problems,
        advance_problems=advance_problems,
    )
