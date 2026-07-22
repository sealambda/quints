"""Tests for the Yapeal CSV beangulp importer."""

from datetime import date as Date
from decimal import Decimal
from pathlib import Path

from beancount.core import data

from beangulp_yapeal import Importer

FIXTURE = str(Path(__file__).parent / "fixture.csv")

CASH = "Assets:Bank:Checking"
RULES = ((r"client ag", "Assets:Receivable", "*"),)


def _importer() -> Importer:
    return Importer(CASH, payee_rules=RULES)


def test_identify_accepts_yapeal_csv(tmp_path: Path) -> None:
    assert _importer().identify(FIXTURE)
    other = tmp_path / "not-a-statement.csv"
    other.write_text("date,amount\n2026-01-01,1.00\n")
    assert not _importer().identify(str(other))
    other.write_text("Transaktions-ID,Betrag\n")
    assert not _importer().identify(str(other))


def test_identify_bails_on_binary(tmp_path: Path) -> None:
    binary = tmp_path / "binary.dat"
    binary.write_bytes(b"\x80\x81\x82")
    assert not _importer().identify(str(binary))


def test_identify_with_iban(tmp_path: Path) -> None:
    importer = Importer(CASH, iban="CH9300762011623852957")
    iban_csv = tmp_path / "with-iban.csv"
    iban_csv.write_text(
        "Transaktions-ID,Belastung oder Gutschrift,Buchungs-Datum,"
        "Transaktions-Datum,Transaktions-Info,Betrag,Belastung (CHF),"
        "Gutschrift (CHF),Kontowährung,Saldo (CHF),Gegenpartei,IBAN Gegenpartei,"
        "Original Transaktionsbetrag,Transaktionswährung,Devisenkurs,Gebühr,"
        "Zahlungstyp,Letzte 4 der Kartennummer,Karteninhaber,Status der Spesen,"
        "Zahlungsreferenz,Eigene Notiz,Zahlungs-Notiz,Zahlung erfasst am,"
        "Zahlung genehmigt von,Zahlung genehmigt am,Kategorie,Karten Programm,"
        "usedTags,mentions,ultimateCreditorName,ultimateDebitorName\n"
        "e9839ff4-e419-432a-b553-801e13c7e8f1,CREDIT,30.12.2025,"
        "30.12.2025,John Kert,100.00,,100.00,CHF,100.00,John Kert,CH9300762011623852957,"
        "100.00,CHF,,"
        ",domestic_iban,,,,f68eb96a17764c4db09912ef65be3b63,,"
        "Subject Test,,,,general,,"
        ",,,\n"
    )
    assert importer.identify(str(iban_csv))

    wrong_iban = Importer(CASH, iban="CH0000000000000000000")
    assert not wrong_iban.identify(str(iban_csv))


def test_identify_iban_matches_spaceless(tmp_path: Path) -> None:
    importer = Importer(CASH, iban="CH9300762011623852957")
    csv_file = tmp_path / "spaceless.csv"
    csv_file.write_text(
        "Transaktions-ID,Belastung oder Gutschrift,Buchungs-Datum,"
        "Transaktions-Datum,Transaktions-Info,Betrag,Belastung (CHF),"
        "Gutschrift (CHF),Kontowährung,Saldo (CHF),Gegenpartei,IBAN Gegenpartei,"
        "Original Transaktionsbetrag,Transaktionswährung,Devisenkurs,Gebühr,"
        "Zahlungstyp,Letzte 4 der Kartennummer,Karteninhaber,Status der Spesen,"
        "Zahlungsreferenz,Eigene Notiz,Zahlungs-Notiz,Zahlung erfasst am,"
        "Zahlung genehmigt von,Zahlung genehmigt am,Kategorie,Karten Programm,"
        "usedTags,mentions,ultimateCreditorName,ultimateDebitorName\n"
        "e9839ff4-e419-432a-b553-801e13c7e8f1,CREDIT,30.12.2025,"
        "30.12.2025,John Kert,100.00,,100.00,CHF,100.00,John Kert,CH9300762011623852957,"
        "100.00,CHF,,"
        ",domestic_iban,,,,f68eb96a17764c4db09912ef65be3b63,,"
        "Subject Test,,,,general,,"
        ",,,\n"
    )
    assert importer.identify(str(csv_file))


def test_identify_iban_ignored_when_none() -> None:
    assert _importer().identify(FIXTURE)


def test_account() -> None:
    assert _importer().account("ignored") == CASH


def test_filename() -> None:
    assert _importer().filename("ignored") == "statement.csv"


def test_extract_transactions() -> None:
    entries = _importer().extract(FIXTURE, existing=[])
    txns = [e for e in entries if isinstance(e, data.Transaction)]
    assert len(txns) == 3

    debit = txns[0]
    assert debit.date == Date(2026, 1, 5)
    assert debit.payee == "ACME Hosting Inc"
    assert debit.narration == "Debit card payment"
    assert debit.flag == "!"  # no rule matched
    assert len(debit.postings) == 1
    assert debit.postings[0].units is not None
    assert debit.postings[0].units.number == Decimal("-25.50")
    assert debit.meta["bank_ref"] == "e9839ff4-e419-432a-b553-801e13c7e8f1"

    credit = txns[1]
    assert credit.date == Date(2026, 1, 10)
    assert credit.payee == "Client AG"
    assert credit.flag == "*"  # rule matched
    assert [p.account for p in credit.postings] == [CASH, "Assets:Receivable"]
    assert credit.postings[1].units is not None
    assert credit.postings[1].units.number == Decimal("-1000.00")

    debit2 = txns[2]
    assert debit2.date == Date(2026, 1, 15)
    assert debit2.meta["bank_ref"] == "a1b2c3d4-5678-90ab-cdef-1234567890ab"


def test_extract_dedup_by_reference() -> None:
    meta = data.new_metadata("ledger.bean", 1)
    meta["bank_ref"] = "e9839ff4-e419-432a-b553-801e13c7e8f1"
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
    assert [t.meta["bank_ref"] for t in txns] == [
        "f68eb96a-1776-4c4d-b099-12ef65be3b63",
        "a1b2c3d4-5678-90ab-cdef-1234567890ab",
    ]


def test_extract_empty_csv(tmp_path: Path) -> None:
    empty = tmp_path / "empty.csv"
    empty.write_text(
        "Transaktions-ID,Belastung oder Gutschrift,Buchungs-Datum,"
        "Transaktions-Datum,Gegenpartei,Transaktions-Info,"
        "Gutschrift (CHF),Belastung (CHF),Betrag\n"
    )
    entries = _importer().extract(str(empty), existing=[])
    assert not entries


def test_betrag_fallback(tmp_path: Path) -> None:
    csv = tmp_path / "betrag.csv"
    csv.write_text(
        "Transaktions-ID,Belastung oder Gutschrift,Buchungs-Datum,"
        "Transaktions-Datum,Gegenpartei,Transaktions-Info,"
        "Gutschrift (CHF),Belastung (CHF),Betrag\n"
        "R1,DEBIT,01.02.2026,01.02.2026,Some Vendor,Payment,,,100.00\n"
        "R2,CREDIT,02.02.2026,02.02.2026,Income,Salary,,,500.00\n"
    )
    entries = _importer().extract(str(csv), existing=[])
    txns = [e for e in entries if isinstance(e, data.Transaction)]
    assert len(txns) == 2
    assert txns[0].postings[0].units is not None
    assert txns[0].postings[0].units.number == Decimal("-100.00")
    assert txns[1].postings[0].units is not None
    assert txns[1].postings[0].units.number == Decimal("500.00")


def test_payee_rules_draft_counter_leg() -> None:
    importer = Importer(CASH, payee_rules=[(r"client", "Assets:Receivable", "*")])
    entries = importer.extract(FIXTURE, existing=[])
    txns = [e for e in entries if isinstance(e, data.Transaction)]
    matched = [t for t in txns if len(t.postings) == 2]
    assert len(matched) == 1
    assert matched[0].payee == "Client AG"
    assert matched[0].postings[1].account == "Assets:Receivable"
