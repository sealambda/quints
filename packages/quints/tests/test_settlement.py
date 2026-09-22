"""Tests for VAT settlement generation and outstanding-liability tracking."""

import dataclasses
from datetime import date
from decimal import Decimal
from pathlib import Path

from quints import ledger, mwst, settlement
from quints.mwst import MwstReport, RateRow


def _report(**overrides: str | Decimal) -> MwstReport:
    z = Decimal("0")
    base = MwstReport(
        date_from="2026-04-01",
        date_to="2026-06-30",
        z200=z,
        z221=z,
        z289=z,
        z299=z,
        z303_net=z,
        z303_tax=Decimal("747.63"),
        z399=Decimal("747.63"),
        z400=Decimal("34.74"),
        z479=Decimal("34.74"),
        z500=Decimal("712.89"),
    )
    report = dataclasses.replace(base, **overrides)
    # The settlement flushes the OutputVAT account, i.e. the rate rows — one
    # standard-rate row is enough to stand in for a quarter of sales here.
    return dataclasses.replace(
        report,
        output_vat=report.z303_tax,
        rate_rows=[
            RateRow("303", "standard", Decimal("0.081"), report.z303_net, report.z303_tax, True)
        ],
    )


def test_build_settlement(tmp_path: Path) -> None:
    led = tmp_path / "m.bean"
    led.write_text("2024-01-01 open Liabilities:CH:GmbH:Tax:PayableVAT CHF\n")
    s = settlement.build_settlement(led, _report(), "2026-Q2")
    assert (s.output_vat, s.input_vat, s.net) == (
        Decimal("747.63"),
        Decimal("34.74"),
        Decimal("712.89"),
    )
    assert s.settle_date == "2026-06-30" and s.assert_date == "2026-07-01"
    assert s.due == "2026-08-29"  # period end + 60 days (Art. 86)
    assert s.link == "VAT-2026-Q2"
    assert s.payable_after == Decimal("-712.89")  # no prior PayableVAT balance
    text = settlement.settlement_text(s)
    assert "^VAT-2026-Q2" in text and "due: 2026-08-29" in text
    # No Bezugsteuer this period → no posting or assertion for it.
    assert "Bezugsteuer" not in text


def test_settlement_with_bezugsteuer(tmp_path: Path) -> None:
    led = tmp_path / "m.bean"
    led.write_text("2024-01-01 open Liabilities:CH:GmbH:Tax:PayableVAT CHF\n")
    report = _report(
        date_from="2026-07-01",
        date_to="2026-09-30",
        z303_tax=Decimal("81.00"),
        z383_tax=Decimal("7.53"),
        z399=Decimal("88.53"),
        z400=Decimal("7.53"),
        z479=Decimal("7.53"),
        z500=Decimal("81.00"),
    )
    s = settlement.build_settlement(led, report, "2026-Q3")
    assert s.output_vat == Decimal("81.00")  # OutputVAT account balance, not z399
    assert s.bezugsteuer == Decimal("7.53")
    assert s.net == Decimal("81.00")
    text = settlement.settlement_text(s)
    assert "Liabilities:CH:GmbH:Tax:Bezugsteuer" in text
    assert "balance Liabilities:CH:GmbH:Tax:Bezugsteuer" in text
    # The flush must balance: -net + output + bezugsteuer - input == 0.
    assert -s.net + s.output_vat + s.bezugsteuer - s.input_vat == 0


_SETTLE = """
2024-01-01 open Liabilities:CH:GmbH:Tax:PayableVAT CHF
2024-01-01 open Assets:CH:GmbH:Current:UBS:CHF CHF
2026-06-30 * "Q2 VAT Settlement" ^VAT-2026-Q2
  due: 2026-08-29
  Liabilities:CH:GmbH:Tax:PayableVAT  -712.89 CHF
  Assets:CH:GmbH:Current:UBS:CHF       712.89 CHF
"""

_PAYMENT = """
2026-08-15 * "ESTV Q2 paid" ^VAT-2026-Q2
  Liabilities:CH:GmbH:Tax:PayableVAT   712.89 CHF
  Assets:CH:GmbH:Current:UBS:CHF      -712.89 CHF
"""


def test_outstanding_unpaid(tmp_path: Path) -> None:
    led = tmp_path / "m.bean"
    led.write_text(_SETTLE)
    libs, unlinked, total, _today = settlement.outstanding(led, today=date(2026, 7, 7))
    assert len(libs) == 1
    assert libs[0].owed == Decimal("712.89")
    assert libs[0].due == "2026-08-29"
    assert libs[0].days_left == 53
    assert total == Decimal("712.89") and unlinked == Decimal("0")


def test_outstanding_clears_after_payment(tmp_path: Path) -> None:
    led = tmp_path / "m.bean"
    led.write_text(_SETTLE + _PAYMENT)
    libs, _unlinked, total, _today = settlement.outstanding(led, today=date(2026, 7, 7))
    assert libs == [] and total == Decimal("0")


def test_credit_period_settles_as_a_claim_on_the_estv(tmp_path: Path) -> None:
    # Ziffer 510: input VAT ran ahead, so the flush *debits* PayableVAT and the
    # balance stands as a receivable from the ESTV until it is refunded.
    led = tmp_path / "m.bean"
    led.write_text("2024-01-01 open Liabilities:CH:GmbH:Tax:PayableVAT CHF\n")
    report = _report(
        z303_tax=Decimal("8.10"),
        z399=Decimal("8.10"),
        z400=Decimal("81.00"),
        z479=Decimal("81.00"),
        z500=Decimal("-72.90"),
        z510=Decimal("72.90"),
    )
    s = settlement.build_settlement(led, report, "2026-Q2")
    assert s.net == Decimal("-72.90")
    assert s.payable_after == Decimal("72.90")  # a debit balance = a claim
    text = settlement.settlement_text(s)
    assert "72.90 CHF" in text
    # The block still balances: −net + output + bezugsteuer − input == 0.
    assert -s.net + s.output_vat + s.bezugsteuer - s.input_vat == 0


_CREDIT_SETTLE = """
2024-01-01 open Liabilities:CH:GmbH:Tax:PayableVAT CHF
2024-01-01 open Assets:CH:GmbH:Tax:InputVAT CHF
2026-06-30 * "Q2 VAT Settlement" ^VAT-2026-Q2
  due: 2026-08-29
  Liabilities:CH:GmbH:Tax:PayableVAT    72.90 CHF
  Assets:CH:GmbH:Tax:InputVAT          -72.90 CHF
"""


def test_status_reports_a_credit_as_a_negative_amount(tmp_path: Path) -> None:
    # A Ziffer-510 quarter leaves PayableVAT with a debit balance; `vat status`
    # must show it as a claim rather than pretend nothing is outstanding.
    led = tmp_path / "m.bean"
    led.write_text(_CREDIT_SETTLE)
    libs, _unlinked, total, _today = settlement.outstanding(led, today=date(2026, 7, 7))
    assert len(libs) == 1 and libs[0].owed == Decimal("-72.90")
    assert total == Decimal("-72.90")


_REAL_QUARTER = """
2026-01-01 open Assets:CH:GmbH:Current:UBS:CHF CHF
  kmu: "1020"
2026-01-01 open Assets:CH:GmbH:Receivable:Trade CHF
  kmu: "1100"
2026-01-01 open Assets:CH:GmbH:Tax:InputVAT CHF
  kmu: "1170"
2026-01-01 open Liabilities:CH:GmbH:Tax:OutputVAT CHF
  kmu: "2200"
2026-01-01 open Liabilities:CH:GmbH:Tax:PayableVAT CHF
  kmu: "2200"
2026-01-01 open Income:CH:GmbH:Consulting:External:Domestic CHF
  kmu: "3400"
2026-01-01 open Income:CH:GmbH:Erloesminderungen CHF
  kmu: "3800"
2026-01-01 open Expenses:CH:GmbH:Material CHF
  kmu: "4400"

2026-07-02 * "Acme AG" "Consulting"
  Assets:CH:GmbH:Receivable:Trade                1081.00 CHF
  Income:CH:GmbH:Consulting:External:Domestic   -1000.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT               -81.00 CHF

2026-07-20 * "Acme AG" "Skonto"
  Income:CH:GmbH:Erloesminderungen                 20.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT                 1.62 CHF
  Assets:CH:GmbH:Receivable:Trade                 -21.62 CHF

2026-08-15 * "Lieferant" "Material"
  Expenses:CH:GmbH:Material                       200.00 CHF
  Assets:CH:GmbH:Tax:InputVAT                      16.20 CHF
  Assets:CH:GmbH:Current:UBS:CHF                 -216.20 CHF
"""


def test_settlement_flushes_the_accounts_the_report_measured(tmp_path: Path) -> None:
    # The settlement must empty the VAT accounts exactly: its OutputVAT debit
    # is the quarter's *net* accrual, so a credit note's reversal (Ziffer 235)
    # is already in it. Anything else and the emitted balance assertions fail.
    led = tmp_path / "m.bean"
    led.write_text(_REAL_QUARTER)
    report = mwst.compute(led, "2026-07-01", "2026-09-30")
    assert report.z235 == Decimal("20.00")
    assert report.z303_tax == Decimal("79.38")  # 81.00 accrued − 1.62 reversed
    assert report.z400 == Decimal("16.20")

    s = settlement.build_settlement(led, report, "2026-Q3")
    assert s.output_vat == Decimal("79.38")  # the OutputVAT account balance
    assert s.input_vat == Decimal("16.20")
    assert s.net == Decimal("63.18")
    assert -s.net + s.output_vat + s.bezugsteuer - s.input_vat == 0

    # Pasting the block back must leave the ledger balanced and its assertions true.
    led.write_text(_REAL_QUARTER + "\n" + settlement.settlement_text(s) + "\n")
    _entries, errors = ledger.load_entries(led)
    assert not errors, errors
