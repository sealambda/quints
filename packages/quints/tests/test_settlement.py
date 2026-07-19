"""Tests for VAT settlement generation and outstanding-liability tracking."""

import dataclasses
from datetime import date
from decimal import Decimal
from pathlib import Path

from quints import settlement
from quints.mwst import MwstReport


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
    return dataclasses.replace(base, **overrides)


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
        z382_tax=Decimal("7.53"),
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
