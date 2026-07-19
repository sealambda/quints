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


def _importer(holder: str | None = None) -> Importer:
    return Importer(ACCOUNTS, fees_account="Expenses:Fees", payee_rules=RULES, holder=holder)


def _number(posting: data.Posting) -> Decimal:
    """Posting amount, asserted present (the importer always drafts one)."""
    assert posting.units is not None and posting.units.number is not None
    return posting.units.number


def test_identify_by_schema_currency_and_holder(tmp_path: Path):
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
    assert _number(card.postings[1]) == Decimal("1.76")

    assert transfer.payee == "Acme OU"
    assert transfer.flag == "*"
    assert transfer.postings[1].account == "Assets:Receivable"
    assert _number(transfer.postings[1]) == Decimal("-878.48")

    assert conversion.meta["conversion"] == "true"
    assert conversion.payee == "Wise"
    assert conversion.flag == "!"  # single leg: incomplete until merged
    assert [p.account for p in conversion.postings] == ["Assets:Wise:EUR", "Expenses:Fees"]
    assert _number(conversion.postings[1]) == Decimal("0.47")

    assert balances[0].date == Date(2026, 7, 11)
    assert balances[0].amount.number == Decimal("7273.46")


def test_merge_conversions_joins_legs_with_priced_incoming_leg():
    importer = _importer()
    entries = importer.extract(EUR, existing=[]) + importer.extract(USD, existing=[])
    merged = merge_conversions(entries)

    conversions = [
        e
        for e in merged
        if isinstance(e, data.Transaction) and e.meta.get("wise_id") == "CONVERSION-3000003"
    ]
    assert len(conversions) == 1
    txn = conversions[0]
    assert txn.flag == "*"
    accounts = [p.account for p in txn.postings]
    assert accounts == ["Assets:Wise:EUR", "Assets:Wise:USD", "Expenses:Fees"]
    out_leg, in_leg, fee = txn.postings
    assert _number(out_leg) == Decimal("-100.00")
    assert _number(in_leg) == Decimal("107.20")
    # @@ 99.53 EUR: what left the EUR balance net of the 0.47 fee
    price = in_leg.price
    assert price is not None and price.number is not None
    assert price.number == Decimal("99.53")
    assert price.currency == "EUR"
    # weights: -100.00 + 99.53 + 0.47 == 0 — the merged txn balances exactly
    assert _number(out_leg) + price.number + _number(fee) == 0

    # both balance assertions survive the merge
    assert sum(isinstance(e, data.Balance) for e in merged) == 2


def test_merge_leaves_unpaired_leg_for_review():
    entries = _importer().extract(EUR, existing=[])
    merged = merge_conversions(entries)
    unpaired = [
        e
        for e in merged
        if isinstance(e, data.Transaction) and e.meta.get("wise_id") == "CONVERSION-3000003"
    ]
    assert len(unpaired) == 1
    assert unpaired[0].flag == "!"


def test_merge_leaves_legs_without_cash_amount_for_review():
    # Regression: a conversion leg whose cash posting has no amount used to
    # crash the merge (None.number); it must be left unmerged for review.
    def leg(index: int, postings: list[data.Posting]) -> data.Transaction:
        meta = data.new_metadata("statement.json", index)
        meta["wise_id"] = "CONVERSION-9"
        meta["conversion"] = "true"
        return data.Transaction(
            meta, Date(2026, 7, 5), "!", "Wise", "", data.EMPTY_SET, data.EMPTY_SET, postings
        )

    from beancount.core.amount import Amount

    broken = leg(0, [data.Posting("Assets:Wise:EUR", None, None, None, None, None)])
    intact = leg(
        1,
        [data.Posting("Assets:Wise:USD", Amount(Decimal("107.20"), "USD"), None, None, None, None)],
    )
    merged = merge_conversions([broken, intact])
    assert merged == [broken, intact]  # both kept, still flagged "!"


def test_extract_skips_references_already_in_ledger():
    meta = data.new_metadata("ledger.bean", 1)
    meta["wise_id"] = "CARD-1000001"
    booked = data.Transaction(
        meta,
        Date(2026, 7, 3),
        "*",
        "Cloudflare",
        "already booked",
        data.EMPTY_SET,
        data.EMPTY_SET,
        [],
    )
    entries = _importer().extract(EUR, existing=[booked])
    refs = [e.meta.get("wise_id") for e in entries if isinstance(e, data.Transaction)]
    assert "CARD-1000001" not in refs
    assert len(refs) == 2
