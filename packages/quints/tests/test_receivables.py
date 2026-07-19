"""Tests for the open-invoice aging (quints receivables)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from quints import config, receivables

LEDGER = """
2024-01-01 open Assets:CH:GmbH:Receivable:Trade
2024-01-01 open Assets:CH:GmbH:Current:UBS:CHF
2024-01-01 open Income:CH:GmbH:Consulting:External:Domestic CHF

2026-05-01 * "ACME" "May invoiced" ^ACME202605
  invoice: "ACME202605"
  Assets:CH:GmbH:Receivable:Trade       1000.00 CHF
  Income:CH:GmbH:Consulting:External:Domestic  -1000.00 CHF

2026-06-01 * "ACME" "May paid (partial)" ^ACME202605
  Assets:CH:GmbH:Current:UBS:CHF         600.00 CHF
  Assets:CH:GmbH:Receivable:Trade       -600.00 CHF

2026-06-05 * "ACME" "June invoiced" ^ACME202606
  invoice: "ACME202606"
  Assets:CH:GmbH:Receivable:Trade        500.00 CHF
  Income:CH:GmbH:Consulting:External:Domestic   -500.00 CHF

2026-06-20 * "ACME" "June paid in full" ^ACME202606
  Assets:CH:GmbH:Current:UBS:CHF         500.00 CHF
  Assets:CH:GmbH:Receivable:Trade       -500.00 CHF
"""


def test_open_invoices_and_aging(tmp_path: Path) -> None:
    led = tmp_path / "m.bean"
    led.write_text(LEDGER)
    open_invoices, _at = receivables.compute(led, date(2026, 7, 1), config.Config())
    assert [o.number for o in open_invoices] == ["ACME202605"]  # 202606 fully paid
    o = open_invoices[0]
    assert o.open_amount == Decimal("400.00")
    assert o.invoice_date == date(2026, 5, 1) and o.age_days == 61
    assert o.payee == "ACME" and o.currency == "CHF"


def test_at_date_excludes_later_payments(tmp_path: Path) -> None:
    led = tmp_path / "m.bean"
    led.write_text(LEDGER)
    open_invoices, _ = receivables.compute(led, date(2026, 5, 15), config.Config())
    assert {(o.number, o.open_amount) for o in open_invoices} == {
        ("ACME202605", Decimal("1000.00")),
    }


def test_posting_level_invoice_metadata_reallocates(tmp_path: Path) -> None:
    led = tmp_path / "m.bean"
    led.write_text(
        LEDGER
        + """
2026-06-25 * "ACME" "May residual settled with June payment (relink)" ^ACME202605 ^ACME202606
  Assets:CH:GmbH:Receivable:Trade       -400.00 CHF
    invoice: "ACME202605"
  Assets:CH:GmbH:Receivable:Trade        400.00 CHF
    invoice: "ACME202606"
"""
    )
    open_invoices, _ = receivables.compute(led, date(2026, 7, 1), config.Config())
    # 202605 closed by the relink; 202606 reopened by it
    assert {(o.number, o.open_amount) for o in open_invoices} == {
        ("ACME202606", Decimal("400.00")),
    }
