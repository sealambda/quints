"""Tests for the MT940 beangulp importer."""

from datetime import date as Date
from decimal import Decimal
from pathlib import Path

import pytest
from beancount.core import data

from beangulp_mt940 import Importer

FIXTURE = str(Path(__file__).parent / "fixture.mt940")

CASH = "Assets:Bank:Checking"
RULES = ((r"client ag", "Assets:Receivable", "*"),)


def _importer(iban: str | None = None) -> Importer:
    return Importer(CASH, payee_rules=RULES, iban=iban)


def test_identify_accepts_mt940_and_filters_by_iban(tmp_path: Path) -> None:
    assert _importer().identify(FIXTURE)
    assert _importer(iban="CH93 0076 2011 6238 5295 7").identify(FIXTURE)
    assert not _importer(iban="CH0000000000000000000").identify(FIXTURE)
    other = tmp_path / "not-a-statement.csv"
    other.write_text("date,amount\n2026-01-01,1.00\n")
    assert not _importer().identify(str(other))


def test_extract_transactions_and_balance() -> None:
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
    assert debit.postings[0].units is not None
    assert debit.postings[0].units.number == Decimal("-25.50")
    assert debit.meta["mt940_ref"] == "REF001"

    credit = txns[1]
    assert credit.flag == "*"  # rule matched
    assert [p.account for p in credit.postings] == [CASH, "Assets:Receivable"]
    assert credit.postings[1].units is not None
    assert credit.postings[1].units.number == Decimal("-200.00")

    balance = balances[0]
    assert balance.date == Date(2026, 2, 1)  # closing date + 1 (begin-of-day)
    assert balance.amount.number == Decimal("274.50")
    assert balance.account == CASH


def test_extract_skips_references_already_in_ledger() -> None:
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


def test_statement_date_is_closing_date() -> None:
    assert _importer().date(FIXTURE) == Date(2026, 1, 31)


def _statement(tmp_path: Path, body: str) -> str:
    path = tmp_path / "statement.mt940"
    path.write_text(body)
    return str(path)


def _numbers(entries: data.Entries) -> list[Decimal]:
    return [
        p.units.number
        for e in entries
        if isinstance(e, data.Transaction)
        for p in e.postings[:1]
        if p.units is not None and p.units.number is not None
    ]


def test_nonref_placeholder_is_never_a_dedup_key(tmp_path: Path) -> None:
    """``NONREF`` is SWIFT's "no reference" filler, shared by unrelated rows."""
    path = _statement(
        tmp_path,
        ":20:A\n:25:CH9300762011623852957\n:28C:1/1\n:60F:C260101CHF100,00\n"
        ":61:2601050105D10,00NTRFNONREF\n:86:Z?Coffee\n"
        ":61:2601060106D12,00NTRFnonref \n:86:Z?Lunch\n"
        ":62F:C260131CHF78,00\n-",
    )
    meta = data.new_metadata("ledger.bean", 1)
    meta["mt940_ref"] = "NONREF"  # a ledger drafted before this fix
    booked = data.Transaction(
        meta, Date(2025, 12, 1), "*", "", "", data.EMPTY_SET, data.EMPTY_SET, []
    )
    entries = _importer().extract(path, existing=[booked])
    txns = [e for e in entries if isinstance(e, data.Transaction)]
    assert _numbers(entries) == [Decimal("-10.00"), Decimal("-12.00")]
    assert all("mt940_ref" not in t.meta for t in txns)


def test_rejects_a_file_with_statements_for_several_accounts(tmp_path: Path) -> None:
    path = _statement(
        tmp_path,
        ":20:A\n:25:CH9300762011623852957\n:28C:1/1\n:60F:C260101CHF100,00\n"
        ":61:2601050105D10,00NTRFNONREF//A1\n:86:Z?X\n:62F:C260131CHF90,00\n-\n"
        ":20:B\n:25:CH5604835012345678009\n:28C:1/1\n:60F:C260101EUR500,00\n"
        ":61:2601060106D20,00NTRFNONREF//B1\n:86:Z?Y\n:62F:C260131EUR480,00\n-",
    )
    with pytest.raises(ValueError, match="CH5604835012345678009"):
        _importer().extract(path, existing=[])
    with pytest.raises(ValueError, match="one file per account"):
        _importer().date(path)
    # The configured IBAN is in the file, so it is claimed (and then refused
    # loudly) rather than silently left unidentified.
    assert _importer(iban="CH5604835012345678009").identify(path)


def test_several_statements_for_one_account_are_fine(tmp_path: Path) -> None:
    path = _statement(
        tmp_path,
        ":20:A\n:25:CH9300762011623852957\n:28C:1/1\n:60F:C260101CHF100,00\n"
        ":61:2601050105D10,00NTRFNONREF//A1\n:86:Z?X\n:62F:C260131CHF90,00\n-\n"
        ":20:B\n:25:CH93 0076 2011 6238 5295 7\n:28C:2/1\n:60F:C260201CHF90,00\n"
        ":61:2602060206D20,00NTRFNONREF//A2\n:86:Z?Y\n:62F:C260228CHF70,00\n-",
    )
    entries = _importer().extract(path, existing=[])
    assert _numbers(entries) == [Decimal("-10.00"), Decimal("-20.00")]
    balance = entries[-1]
    assert isinstance(balance, data.Balance)
    assert balance.amount.number == Decimal("70.00")


def test_reversals_take_the_opposite_sign(tmp_path: Path) -> None:
    """RC (reversal of credit) is a debit; RD (reversal of debit) a credit."""
    path = _statement(
        tmp_path,
        ":20:X\n:25:CH9300762011623852957\n:28C:1/1\n:60F:C260101CHF100,00\n"
        ":61:2601050105RC25,50NTRFNONREF//R1\n:86:Z?Refund\n"
        ":61:2601060106RD10,00NTRFNONREF//R2\n:86:Z?Chargeback\n"
        ":62F:C260131CHF84,50\n-",
    )
    entries = _importer().extract(path, existing=[])
    assert _numbers(entries) == [Decimal("-25.50"), Decimal("10.00")]
    assert Decimal("100.00") + sum(_numbers(entries)) == Decimal("84.50")


def test_currency_is_never_guessed(tmp_path: Path) -> None:
    """Without a balance tag the statement carries no currency at all."""
    path = _statement(
        tmp_path,
        ":20:X\n:25:CH9300762011623852957\n:28C:1/1\n"
        ":61:2601050105D25,50NTRFNONREF//R1\n:86:Z?ACME\n-",
    )
    with pytest.raises(ValueError, match="currency"):
        _importer().extract(path, existing=[])
    entries = Importer(CASH, currency="EUR").extract(path, existing=[])
    txn = entries[0]
    assert isinstance(txn, data.Transaction) and txn.postings[0].units is not None
    assert txn.postings[0].units.currency == "EUR"
