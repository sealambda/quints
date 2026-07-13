"""Tests for the MT940 beangulp importer."""

from datetime import date as Date
from decimal import Decimal
from pathlib import Path

from beancount.core import data

from beangulp_mt940 import Importer

FIXTURE = str(Path(__file__).parent / "fixture.mt940")

CASH = "Assets:Bank:Checking"
RULES = ((r"client ag", "Assets:Receivable", "*"),)


def _importer(**kwargs):
    return Importer(CASH, payee_rules=RULES, **kwargs)


def test_identify_accepts_mt940_and_filters_by_iban(tmp_path):
    assert _importer().identify(FIXTURE)
    assert _importer(iban="CH93 0076 2011 6238 5295 7").identify(FIXTURE)
    assert not _importer(iban="CH0000000000000000000").identify(FIXTURE)
    other = tmp_path / "not-a-statement.csv"
    other.write_text("date,amount\n2026-01-01,1.00\n")
    assert not _importer().identify(str(other))


def test_extract_transactions_and_balance():
    entries = _importer().extract(FIXTURE, existing=[])
    txns = [e for e in entries if isinstance(e, data.Transaction)]
    balances = [e for e in entries if isinstance(e, data.Balance)]
    assert len(txns) == 2  # zero-amount fee closing is skipped
    assert len(balances) == 1

    debit = txns[0]
    assert debit.date == Date(2026, 1, 5)
    assert debit.payee == "ACME Hosting Inc"
    assert debit.narration == "Debit card payment"
    assert debit.flag == "!"  # no rule matched → review flag, cash leg only
    assert len(debit.postings) == 1
    assert debit.postings[0].units.number == Decimal("-25.50")
    assert debit.meta["mt940_ref"] == "REF001"

    credit = txns[1]
    assert credit.flag == "*"  # rule matched
    assert [p.account for p in credit.postings] == [CASH, "Assets:Receivable"]
    assert credit.postings[1].units.number == Decimal("-200.00")

    balance = balances[0]
    assert balance.date == Date(2026, 2, 1)  # closing date + 1 (begin-of-day)
    assert balance.amount.number == Decimal("274.50")
    assert balance.account == CASH


def test_extract_skips_references_already_in_ledger():
    meta = data.new_metadata("ledger.bean", 1)
    meta["mt940_ref"] = "REF001"
    booked = data.Transaction(
        meta,
        Date(2026, 1, 5),
        "*",
        "ACME",
        "already booked",
        data.EMPTY_SET,
        data.EMPTY_SET,
        [],
    )
    entries = _importer().extract(FIXTURE, existing=[booked])
    txns = [e for e in entries if isinstance(e, data.Transaction)]
    assert [t.meta["mt940_ref"] for t in txns] == ["REF002"]


def test_statement_date_is_closing_date():
    assert _importer().date(FIXTURE) == Date(2026, 1, 31)
