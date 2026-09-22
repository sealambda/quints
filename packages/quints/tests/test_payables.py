"""Tests for the open-bill aging (quints payables)."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

from quints import config, payables

LEDGER = """
2024-01-01 open Liabilities:CH:GmbH:Payable:Trade
2024-01-01 open Assets:CH:GmbH:Current:UBS:CHF
2024-01-01 open Expenses:CH:GmbH:Admin:Bookkeeping

2026-05-01 * "Treuhand Muster" "Bookkeeping Q1" ^TM-2026-4711
  bill: "TM-2026-4711"
  due: 2026-05-31
  Expenses:CH:GmbH:Admin:Bookkeeping     1000.00 CHF
  Liabilities:CH:GmbH:Payable:Trade     -1000.00 CHF

2026-06-01 * "Treuhand Muster" "Part payment" ^TM-2026-4711
  Liabilities:CH:GmbH:Payable:Trade       600.00 CHF
  Assets:CH:GmbH:Current:UBS:CHF         -600.00 CHF

2026-06-05 * "Hoster AG" "Hosting June" ^HOST-77
  bill: "HOST-77"
  Expenses:CH:GmbH:Admin:Bookkeeping      500.00 CHF
  Liabilities:CH:GmbH:Payable:Trade      -500.00 CHF

2026-06-20 * "Hoster AG" "Paid HOST-77" ^HOST-77
  Liabilities:CH:GmbH:Payable:Trade       500.00 CHF
  Assets:CH:GmbH:Current:UBS:CHF         -500.00 CHF
"""


def test_open_bills_are_aged_against_the_due_date(tmp_path: Path) -> None:
    led = tmp_path / "m.bean"
    led.write_text(LEDGER)
    open_bills, _cons, _at = payables.compute(led, date(2026, 7, 1), config.Config())
    assert [b.number for b in open_bills] == ["TM-2026-4711"]  # HOST-77 is paid
    b = open_bills[0]
    assert b.open_amount == Decimal("400.00")  # positive: what is still owed
    assert b.keyed_by == payables.KEY_BILL
    assert (b.bill_date, b.due_date) == (date(2026, 5, 1), date(2026, 5, 31))
    assert b.days_overdue == 31
    assert b.payee == "Treuhand Muster" and b.currency == "CHF"


def test_the_clearing_payment_only_carries_the_link(tmp_path: Path) -> None:
    # The part payment above has no `bill:` metadata — a lone ^link keys it,
    # which is what a hand-written or imported clearing leg looks like.
    led = tmp_path / "m.bean"
    led.write_text(LEDGER)
    open_bills, _cons, _ = payables.compute(led, date(2026, 5, 15), config.Config())
    assert [(b.number, b.open_amount) for b in open_bills] == [
        ("TM-2026-4711", Decimal("1000.00"))  # the payment is later than `at`
    ]


def test_due_date_falls_back_to_the_configured_terms(tmp_path: Path) -> None:
    led = tmp_path / "m.bean"
    led.write_text(LEDGER.replace("2026-06-20", "2026-09-20"))  # leave HOST-77 open
    cfg = config.Config()
    (host,) = [b for b in payables.compute(led, date(2026, 7, 1), cfg)[0] if b.number == "HOST-77"]
    assert host.due_date == date(2026, 7, 5)  # billed 06-05 + 30 days, no `due:`
    assert host.days_overdue == -4  # not due yet

    short = replace(cfg, payables_default_terms_days=10)
    (host,) = [
        b for b in payables.compute(led, date(2026, 7, 1), short)[0] if b.number == "HOST-77"
    ]
    assert host.due_date == date(2026, 6, 15) and host.days_overdue == 16


NO_ID = """
2024-01-01 open Liabilities:CH:GmbH:Payable:Trade
2024-01-01 open Assets:CH:GmbH:Current:UBS:CHF
2024-01-01 open Expenses:CH:GmbH:Admin:Bookkeeping

2026-06-10 * "Kaffee GmbH" "Office coffee"
  Expenses:CH:GmbH:Admin:Bookkeeping       80.00 CHF
  Liabilities:CH:GmbH:Payable:Trade       -80.00 CHF

2026-06-11 * "Papeterie Bern" "Paper" ^PROJ2024-A ^FY2026-Q2
  Expenses:CH:GmbH:Admin:Bookkeeping       25.00 CHF
  Liabilities:CH:GmbH:Payable:Trade       -25.00 CHF
"""


def test_a_bill_with_no_id_falls_back_to_payee_and_amount(tmp_path: Path) -> None:
    # Neither `bill:` nor a lone link (two links name a project and a period,
    # not the bill), so the group key says out loud what holds it together.
    led = tmp_path / "m.bean"
    led.write_text(NO_ID)
    open_bills, _cons, _ = payables.compute(led, date(2026, 6, 30), config.Config())
    assert [(b.number, b.keyed_by) for b in open_bills] == [
        ("Kaffee GmbH · 80.00", payables.KEY_FALLBACK),
        ("Papeterie Bern · 25.00", payables.KEY_FALLBACK),
    ]


def test_the_fallback_key_still_nets_against_its_payment(tmp_path: Path) -> None:
    # The bill is booked with one decimal, the payment with two — the key is
    # written to the Rappen, so they still meet.
    led = tmp_path / "m.bean"
    led.write_text(
        NO_ID.replace("80.00 CHF", "80.0 CHF")
        + """
2026-06-25 * "Kaffee GmbH" "Paid"
  Liabilities:CH:GmbH:Payable:Trade        80.00 CHF
  Assets:CH:GmbH:Current:UBS:CHF          -80.00 CHF
"""
    )
    open_bills, _cons, _ = payables.compute(led, date(2026, 6, 30), config.Config())
    assert [b.number for b in open_bills] == ["Papeterie Bern · 25.00"]


_PROJECT_LINKED = """
2024-01-01 open Liabilities:CH:GmbH:Payable:Trade
2024-01-01 open Assets:CH:GmbH:Current:UBS:CHF
2024-01-01 open Expenses:CH:GmbH:Admin:Bookkeeping

2026-06-01 * "Treuhand Muster" "Bookkeeping" ^TM-2026-4711
  bill: "TM-2026-4711"
  Expenses:CH:GmbH:Admin:Bookkeeping      480.00 CHF
  Liabilities:CH:GmbH:Payable:Trade      -480.00 CHF

2026-06-15 * "Treuhand Muster" "Paid" ^PROJ2024-A
  Liabilities:CH:GmbH:Payable:Trade       480.00 CHF
  Assets:CH:GmbH:Current:UBS:CHF         -480.00 CHF
"""


def test_a_lone_link_is_read_as_the_bill_id_whatever_it_names(tmp_path: Path) -> None:
    # Suppliers number their invoices however they like, so a lone link is
    # trusted by its shape being irrelevant — which is also its cost: a
    # payment linked only to a project keys on the project. The remedy is
    # `bill:`, which always wins, and nothing is lost quietly: the cleared
    # bill and the stray key both stand on the report.
    led = tmp_path / "m.bean"
    led.write_text(_PROJECT_LINKED)
    stray = payables.compute(led, date(2026, 6, 30), config.Config())[0]
    assert [(b.number, b.open_amount) for b in stray] == [
        ("TM-2026-4711", Decimal("480.00")),  # still owed, as far as the key knows
        ("PROJ2024-A", Decimal("-480.00")),  # the payment, keyed on the project link
    ]

    led.write_text(
        _PROJECT_LINKED.replace('"Paid" ^PROJ2024-A', '"Paid" ^PROJ2024-A\n  bill: "TM-2026-4711"')
    )
    assert payables.compute(led, date(2026, 6, 30), config.Config())[0] == []


def test_posting_level_bill_metadata_splits_a_lump_sum_payment(tmp_path: Path) -> None:
    led = tmp_path / "m.bean"
    led.write_text(
        LEDGER
        + """
2026-06-28 * "Treuhand Muster" "Lump sum for two bills"
  Liabilities:CH:GmbH:Payable:Trade       400.00 CHF
    bill: "TM-2026-4711"
  Liabilities:CH:GmbH:Payable:Trade       200.00 CHF
    bill: "TM-2026-4712"
  Assets:CH:GmbH:Current:UBS:CHF         -600.00 CHF
"""
    )
    open_bills, _cons, _ = payables.compute(led, date(2026, 7, 1), config.Config())
    # 4711 settled by its share; 4712 was never booked, so the payment against
    # it shows up as an overpayment — visible, not silently swallowed.
    assert [(b.number, b.open_amount) for b in open_bills] == [("TM-2026-4712", Decimal("-200.00"))]
    # keyed by the posting's `bill:`, though no billing leg was ever booked
    assert open_bills[0].keyed_by == payables.KEY_BILL


MULTI_CURRENCY = """
2024-01-01 open Liabilities:CH:GmbH:Payable:Trade
2024-01-01 open Expenses:CH:GmbH:IT:Hosting

2026-06-01 * "Foreign SaaS" "Hosting" ^FS-9001
  bill: "FS-9001"
  Expenses:CH:GmbH:IT:Hosting           1000.00 EUR
  Liabilities:CH:GmbH:Payable:Trade

2026-06-10 * "Local AG" "Services" ^LA-22
  bill: "LA-22"
  Expenses:CH:GmbH:IT:Hosting            500.00 CHF
  Liabilities:CH:GmbH:Payable:Trade

2026-06-28 price EUR 0.93 CHF
"""


def test_consolidation_converts_at_the_latest_rate(tmp_path: Path) -> None:
    led = tmp_path / "m.bean"
    led.write_text(MULTI_CURRENCY)
    _open, cons, _ = payables.compute(led, date(2026, 7, 1), config.Config())
    assert cons.currency == "CHF" and cons.missing == []
    assert cons.grand_total == Decimal("1430.00")  # 1000 EUR at 0.93 + 500 CHF
    eur = next(ct for ct in cons.totals if ct.currency == "EUR")
    assert eur.total == Decimal("1000.00") and eur.converted == Decimal("930.00")
    assert eur.rate == Decimal("0.93") and eur.rate_date == date(2026, 6, 28)


def test_consolidation_missing_rate_is_excluded_and_reported(tmp_path: Path) -> None:
    led = tmp_path / "m.bean"
    led.write_text(MULTI_CURRENCY.replace("2026-06-28 price EUR 0.93 CHF", ""))
    _open, cons, _ = payables.compute(led, date(2026, 7, 1), config.Config())
    assert cons.missing == ["EUR"] and cons.grand_total == Decimal("500.00")


def test_nothing_open_when_every_bill_is_paid(tmp_path: Path) -> None:
    led = tmp_path / "m.bean"
    led.write_text(LEDGER.replace("600.00 CHF", "1000.00 CHF").replace("-600.00", "-1000.00"))
    open_bills, _cons, _ = payables.compute(led, date(2026, 7, 1), config.Config())
    assert open_bills == []


def test_booked_bill_recognises_only_the_billing_leg(tmp_path: Path) -> None:
    from beancount.core import data

    from quints import ledger as ledger_mod

    led = tmp_path / "m.bean"
    led.write_text(LEDGER)
    entries, _errors = ledger_mod.load_entries(led)
    txns = [e for e in entries if isinstance(e, data.Transaction)]
    keys = [payables.booked_bill(e, config.Config()) for e in txns]
    # bill, payment, bill, payment — only the two credits are bills
    assert keys == ["TM-2026-4711", None, "HOST-77", None]
