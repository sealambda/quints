"""Tests for the Wise balance-statement importer."""

from datetime import date as Date
from decimal import Decimal
from pathlib import Path

from beancount.core import data
from beangulp_wise import Importer, merge_conversions

HERE = Path(__file__).parent
EUR = str(HERE / "fixture-eur.json")
USD = str(HERE / "fixture-usd.json")

ACCOUNTS = {"EUR": "Assets:Wise:EUR", "USD": "Assets:Wise:USD"}
RULES = (
    (r"cloudflare", "Expenses:IT:Hosting", "!"),
    (r"acme", "Assets:Receivable", "*"),
)


def _importer(**kwargs):
    kwargs.setdefault("fees_account", "Expenses:Fees")
    kwargs.setdefault("payee_rules", RULES)
    return Importer(ACCOUNTS, **kwargs)


def test_identify_by_schema_currency_and_holder(tmp_path):
    assert _importer().identify(EUR)
    assert _importer(holder="Test GmbH").identify(EUR)
    assert not _importer(holder="Other AG").identify(EUR)
    assert not Importer({"CHF": "Assets:Wise:CHF"}).identify(EUR)
    other = tmp_path / "other.json"
    other.write_text('{"not": "a statement"}')
    assert not _importer().identify(str(other))


def test_extract_card_transfer_conversion_and_balance():
    entries = _importer().extract(EUR, existing=[])
    txns = [e for e in entries if isinstance(e, data.Transaction)]
    balances = [e for e in entries if isinstance(e, data.Balance)]
    assert len(txns) == 3  # zero-value adjustment skipped
    assert len(balances) == 1

    card, transfer, conversion = txns
    assert card.date == Date(2026, 7, 3)
    assert card.payee == "Cloudflare"
    assert card.flag == "!"
    assert card.meta["wise_id"] == "CARD-1000001"
    assert [p.account for p in card.postings] == ["Assets:Wise:EUR", "Expenses:IT:Hosting"]
    assert card.postings[1].units.number == Decimal("1.76")

    assert transfer.payee == "Acme OU"
    assert transfer.flag == "*"
    assert transfer.postings[1].account == "Assets:Receivable"
    assert transfer.postings[1].units.number == Decimal("-878.48")

    assert conversion.meta["conversion"] == "true"
    assert conversion.payee == "Wise"
    assert conversion.flag == "!"  # single leg: incomplete until merged
    assert [p.account for p in conversion.postings] == ["Assets:Wise:EUR", "Expenses:Fees"]
    assert conversion.postings[1].units.number == Decimal("0.47")

    assert balances[0].date == Date(2026, 7, 11)
    assert balances[0].amount.number == Decimal("7273.46")


def test_merge_conversions_joins_legs_with_priced_incoming_leg():
    importer = _importer()
    entries = importer.extract(EUR, existing=[]) + importer.extract(USD, existing=[])
    merged = merge_conversions(entries)

    conversions = [
        e for e in merged
        if isinstance(e, data.Transaction) and e.meta.get("wise_id") == "CONVERSION-3000003"
    ]
    assert len(conversions) == 1
    txn = conversions[0]
    assert txn.flag == "*"
    accounts = [p.account for p in txn.postings]
    assert accounts == ["Assets:Wise:EUR", "Assets:Wise:USD", "Expenses:Fees"]
    out_leg, in_leg, fee = txn.postings
    assert out_leg.units.number == Decimal("-100.00")
    assert in_leg.units.number == Decimal("107.20")
    # @@ 99.53 EUR: what left the EUR balance net of the 0.47 fee
    assert in_leg.price.number == Decimal("99.53")
    assert in_leg.price.currency == "EUR"
    # weights: -100.00 + 99.53 + 0.47 == 0 — the merged txn balances exactly
    assert out_leg.units.number + in_leg.price.number + fee.units.number == 0

    # both balance assertions survive the merge
    assert sum(isinstance(e, data.Balance) for e in merged) == 2


def test_merge_leaves_unpaired_leg_for_review():
    entries = _importer().extract(EUR, existing=[])
    merged = merge_conversions(entries)
    unpaired = [
        e for e in merged
        if isinstance(e, data.Transaction) and e.meta.get("wise_id") == "CONVERSION-3000003"
    ]
    assert len(unpaired) == 1
    assert unpaired[0].flag == "!"


def test_extract_skips_references_already_in_ledger():
    meta = data.new_metadata("ledger.bean", 1)
    meta["wise_id"] = "CARD-1000001"
    booked = data.Transaction(
        meta, Date(2026, 7, 3), "*", "Cloudflare", "already booked",
        data.EMPTY_SET, data.EMPTY_SET, [],
    )
    entries = _importer().extract(EUR, existing=[booked])
    refs = [e.meta.get("wise_id") for e in entries if isinstance(e, data.Transaction)]
    assert "CARD-1000001" not in refs
    assert len(refs) == 2
