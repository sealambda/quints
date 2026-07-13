"""Tests for the MWST report computation (Form 310 Ziffern)."""

from decimal import Decimal

from quints import mwst

_LEDGER = """
2024-01-01 open Income:CH:GmbH:Consulting:External:Domestic CHF
2024-01-01 open Income:CH:GmbH:Consulting:External:Export
2024-01-01 open Assets:CH:GmbH:Receivable:Trade
2024-01-01 open Assets:CH:GmbH:Tax:InputVAT CHF
2024-01-01 open Liabilities:CH:GmbH:Tax:OutputVAT CHF
2024-01-01 open Liabilities:CH:GmbH:Tax:Bezugsteuer CHF
2024-01-01 open Expenses:CH:GmbH:IT:Hosting
2024-01-01 open Assets:CH:GmbH:Current:Wise:EUR EUR

2026-07-01 price EUR 0.93 CHF

2026-07-02 * "Client" "Consulting"
  Assets:CH:GmbH:Receivable:Trade                1081.00 CHF
  Income:CH:GmbH:Consulting:External:Domestic   -1000.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT               -81.00 CHF

2026-07-03 * "Abroad Client" "Export consulting"
  Assets:CH:GmbH:Receivable:Trade                 200.00 EUR
  Income:CH:GmbH:Consulting:External:Export      -200.00 EUR

2026-07-04 * "Foreign SaaS" "reverse charge"
  Expenses:CH:GmbH:IT:Hosting                     100.00 EUR
  Assets:CH:GmbH:Tax:InputVAT                       7.53 CHF @@ 8.10 EUR
  Liabilities:CH:GmbH:Tax:Bezugsteuer              -7.53 CHF @@ 8.10 EUR
  Assets:CH:GmbH:Current:Wise:EUR                -100.00 EUR
"""


def _compute(tmp_path):
    led = tmp_path / "m.bean"
    led.write_text(_LEDGER)
    return mwst.compute(led, "2026-07-01", "2026-09-30")


def test_bezugsteuer_ziffer_382(tmp_path):
    r = _compute(tmp_path)
    assert r.z382_tax == Decimal("7.53")
    assert r.z382_net == Decimal("92.96")  # 7.53 / 0.081, rappen-rounded
    assert len(r.bezugsteuer_lines) == 1
    line = r.bezugsteuer_lines[0]
    assert (line.original, line.currency) == (Decimal("8.10"), "EUR")


def test_totals_include_bezugsteuer(tmp_path):
    r = _compute(tmp_path)
    assert r.z303_tax == Decimal("81.00")
    assert r.z399 == Decimal("88.53")  # 81.00 output + 7.53 Bezugsteuer
    assert r.z400 == r.z479 == Decimal("7.53")  # the deduction side
    assert r.z500 == Decimal("81.00")  # Bezugsteuer is cash-neutral
    # Bezugsteuer purchases are not turnover: 200 = domestic + export only.
    assert r.z299 == Decimal("1000.00")
    assert r.z200 == Decimal("1000.00") + r.z221


def test_settlement_debit_is_not_an_accrual(tmp_path):
    led = tmp_path / "m.bean"
    led.write_text(
        _LEDGER
        + """
2026-09-30 * "2026-Q3 VAT Settlement" ^VAT-2026-Q3
  Liabilities:CH:GmbH:Tax:PayableVAT              -81.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT                81.00 CHF
  Liabilities:CH:GmbH:Tax:Bezugsteuer               7.53 CHF
  Assets:CH:GmbH:Tax:InputVAT                      -7.53 CHF

2024-01-01 open Liabilities:CH:GmbH:Tax:PayableVAT CHF
"""
    )
    r = mwst.compute(led, "2026-07-01", "2026-09-30")
    assert r.z382_tax == Decimal("7.53")  # unchanged by the settlement debit
    assert r.z399 == Decimal("88.53")
