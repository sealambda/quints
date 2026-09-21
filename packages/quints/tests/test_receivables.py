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
    open_invoices, _cons, _at = receivables.compute(led, date(2026, 7, 1), config.Config())
    assert [o.number for o in open_invoices] == ["ACME202605"]  # 202606 fully paid
    o = open_invoices[0]
    assert o.open_amount == Decimal("400.00")
    assert o.invoice_date == date(2026, 5, 1) and o.age_days == 61
    assert o.payee == "ACME" and o.currency == "CHF"


def test_at_date_excludes_later_payments(tmp_path: Path) -> None:
    led = tmp_path / "m.bean"
    led.write_text(LEDGER)
    open_invoices, _cons, _ = receivables.compute(led, date(2026, 5, 15), config.Config())
    assert {(o.number, o.open_amount) for o in open_invoices} == {
        ("ACME202605", Decimal("1000.00")),
    }


def test_a_suffixed_invoice_link_is_still_an_invoice(tmp_path: Path) -> None:
    """A hand-written leg carries only the `^link`. A numbering scheme that
    suffixes a re-issue or a credit note (`ACAD202608B`) must not drop out of
    receivables because the id pattern was too strict."""
    led = tmp_path / "m.bean"
    led.write_text(
        LEDGER
        + """
2026-06-30 * "Academy" "June invoiced" ^ACAD202608B
  Assets:CH:GmbH:Receivable:Trade        250.00 CHF
  Income:CH:GmbH:Consulting:External:Domestic  -250.00 CHF
"""
    )
    open_invoices, _cons, _ = receivables.compute(led, date(2026, 7, 1), config.Config())
    assert ("ACAD202608B", Decimal("250.00")) in {(o.number, o.open_amount) for o in open_invoices}


def test_a_second_non_invoice_link_does_not_make_the_invoice_ambiguous(tmp_path: Path) -> None:
    """Only a *lone* invoice-shaped link is trusted, so the id pattern must
    not also match the project or contract links booked next to it."""
    led = tmp_path / "m.bean"
    led.write_text(
        LEDGER
        + """
2026-06-30 * "Academy" "June invoiced" ^ACAD202608B ^PROJECT2024-PHASE2 ^PROJ2024-A ^FY2024-Q1
  Assets:CH:GmbH:Receivable:Trade        250.00 CHF
  Income:CH:GmbH:Consulting:External:Domestic  -250.00 CHF
"""
    )
    open_invoices, _cons, _ = receivables.compute(led, date(2026, 7, 1), config.Config())
    assert "ACAD202608B" in {o.number for o in open_invoices}


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
    open_invoices, _cons, _ = receivables.compute(led, date(2026, 7, 1), config.Config())
    # 202605 closed by the relink; 202606 reopened by it
    assert {(o.number, o.open_amount) for o in open_invoices} == {
        ("ACME202606", Decimal("400.00")),
    }


MULTI_CURRENCY = """
2024-01-01 open Assets:CH:GmbH:Receivable:Trade
2024-01-01 open Income:CH:GmbH:Consulting:External:Export

2026-06-01 * "Globex" "June" ^GLOBEX202606
  invoice: "GLOBEX202606"
  Assets:CH:GmbH:Receivable:Trade       1000.00 EUR
  Income:CH:GmbH:Consulting:External:Export

2026-06-10 * "Initech" "June" ^INITECH202606
  invoice: "INITECH202606"
  Assets:CH:GmbH:Receivable:Trade        500.00 CHF
  Income:CH:GmbH:Consulting:External:Export

2026-06-28 price EUR 0.93 CHF
"""


def test_consolidation_converts_at_latest_rate(tmp_path: Path) -> None:
    led = tmp_path / "m.bean"
    led.write_text(MULTI_CURRENCY)
    _open, cons, _ = receivables.compute(led, date(2026, 7, 1), config.Config())
    assert cons.currency == "CHF" and cons.missing == []
    assert cons.grand_total == Decimal("1430.00")  # 1000 EUR at 0.93 + 500 CHF
    eur = next(ct for ct in cons.totals if ct.currency == "EUR")
    assert eur.total == Decimal("1000.00")
    assert eur.converted == Decimal("930.00")
    assert eur.rate == Decimal("0.93") and eur.rate_date == date(2026, 6, 28)


def test_consolidation_missing_rate_is_excluded_and_reported(tmp_path: Path) -> None:
    led = tmp_path / "m.bean"
    led.write_text(MULTI_CURRENCY.replace("2026-06-28 price EUR 0.93 CHF", ""))
    _open, cons, _ = receivables.compute(led, date(2026, 7, 1), config.Config())
    assert cons.missing == ["EUR"]
    assert cons.grand_total == Decimal("500.00")  # the CHF part only
    eur = next(ct for ct in cons.totals if ct.currency == "EUR")
    assert eur.converted is None and eur.rate is None
