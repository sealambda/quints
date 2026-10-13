"""Tests for the Stripe balance-transaction importer."""

import json
from datetime import date as Date
from decimal import Decimal
from pathlib import Path

import pytest
from beancount.core import data

from beangulp_stripe import Importer, major_units

HERE = Path(__file__).parent
FIXTURE = str(HERE / "fixture-balance-transactions.json")

ACCOUNTS = {"EUR": "Assets:Stripe:EUR"}
RULES = (
    (r"payout", "Assets:Transfer:Stripe", "*"),
    (r"acme|beta", "Income:SaaS", "!"),
)


def _importer(account_id: str | None = None) -> Importer:
    return Importer(
        ACCOUNTS,
        fees_account="Expenses:Fees",
        tax_account="Assets:Tax:InputVAT",
        payee_rules=RULES,
        account_id=account_id,
    )


def _units(posting: data.Posting) -> Decimal:
    assert posting.units is not None and posting.units.number is not None
    return posting.units.number


def test_major_units_handles_currency_exponents() -> None:
    assert major_units(14900, "eur") == Decimal("149.00")
    assert major_units(-621, "EUR") == Decimal("-6.21")
    assert major_units(500, "jpy") == Decimal("500")
    assert major_units(1250, "kwd") == Decimal("1.250")


def test_identify_by_schema_currency_and_account(tmp_path: Path) -> None:
    assert _importer().identify(FIXTURE)
    assert _importer(account_id="acct_TEST123").identify(FIXTURE)
    assert not _importer(account_id="acct_OTHER").identify(FIXTURE)
    assert not Importer({"CHF": "Assets:Stripe:CHF"}).identify(FIXTURE)
    junk = tmp_path / "junk.json"
    junk.write_text('{"not": "balance transactions"}')
    assert not _importer().identify(str(junk))
    bare = tmp_path / "bare.json"
    bare.write_text('[{"object": "balance_transaction", "currency": "eur", "net": 1}]')
    assert _importer().identify(str(bare))


def test_extract_charge_fee_payment_payout_and_balance() -> None:
    entries = _importer().extract(FIXTURE, existing=[])
    txns = [e for e in entries if isinstance(e, data.Transaction)]
    balances = [e for e in entries if isinstance(e, data.Balance)]
    assert len(txns) == 4  # zero adjustment skipped
    assert len(balances) == 1

    charge, monthly_fee, payment, payout = txns

    # Per-charge processing fee split out; income counter leg is the gross.
    assert charge.date == Date(2026, 5, 15)
    assert charge.payee == "ACME Labs GmbH"
    assert charge.flag == "!"
    assert charge.meta["stripe_id"] == "txn_charge_may"
    assert [(p.account, _units(p)) for p in charge.postings] == [
        ("Assets:Stripe:EUR", Decimal("143.91")),
        ("Expenses:Fees", Decimal("5.09")),
        ("Income:SaaS", Decimal("-149.00")),
    ]

    # Separately billed usage fee: VAT fee_detail → tax account, counter leg
    # net of tax, transaction balances, still review-flagged.
    assert monthly_fee.date == Date(2026, 5, 31)
    assert monthly_fee.payee == "Stripe"
    assert monthly_fee.flag == "!"
    assert [(p.account, _units(p)) for p in monthly_fee.postings] == [
        ("Assets:Stripe:EUR", Decimal("-1.12")),
        ("Assets:Tax:InputVAT", Decimal("0.08")),
        ("Expenses:Fees", Decimal("1.04")),
    ]
    assert sum(_units(p) for p in monthly_fee.postings) == 0

    # Per-transaction fee split: net cash, explicit fee, gross counter leg.
    assert payment.payee == "Beta AG"
    assert [(p.account, _units(p)) for p in payment.postings] == [
        ("Assets:Stripe:EUR", Decimal("96.80")),
        ("Expenses:Fees", Decimal("3.20")),
        ("Income:SaaS", Decimal("-100.00")),
    ]

    assert payout.payee == "Stripe"
    assert payout.flag == "*"
    assert [(p.account, _units(p)) for p in payout.postings] == [
        ("Assets:Stripe:EUR", Decimal("-200.00")),
        ("Assets:Transfer:Stripe", Decimal("200.00")),
    ]

    # available + pending per mapped currency (USD is 0, so unmapped is fine).
    assert balances[0].date == Date(2026, 7, 11)
    assert balances[0].account == "Assets:Stripe:EUR"
    assert balances[0].amount.number == Decimal("286.58")


def test_extract_skips_ids_already_in_the_ledger() -> None:
    booked = data.Transaction(
        {"stripe_id": "txn_charge_may"},
        Date(2026, 5, 15),
        "*",
        "ACME Labs GmbH",
        "",
        data.EMPTY_SET,
        data.EMPTY_SET,
        [],
    )
    entries = _importer().extract(FIXTURE, existing=[booked])
    ids = [e.meta.get("stripe_id") for e in entries if isinstance(e, data.Transaction)]
    assert ids == ["txn_fee_may", "txn_payment_jun", "txn_payout_jun"]


def test_without_rules_drafts_keep_cash_and_fee_legs_only() -> None:
    entries = Importer(ACCOUNTS, fees_account="Expenses:Fees").extract(FIXTURE, existing=[])
    charge = next(
        e
        for e in entries
        if isinstance(e, data.Transaction) and e.meta["stripe_id"] == "txn_charge_may"
    )
    assert charge.flag == "!"
    assert [p.account for p in charge.postings] == ["Assets:Stripe:EUR", "Expenses:Fees"]


def test_without_tax_account_tax_folds_into_fees_but_still_balances() -> None:
    entries = Importer(ACCOUNTS, fees_account="Expenses:Fees").extract(FIXTURE, existing=[])
    fee = next(
        e
        for e in entries
        if isinstance(e, data.Transaction) and e.meta["stripe_id"] == "txn_fee_may"
    )
    assert [(p.account, _units(p)) for p in fee.postings] == [
        ("Assets:Stripe:EUR", Decimal("-1.12")),
        ("Expenses:Fees", Decimal("0.08")),
        ("Expenses:Fees", Decimal("1.04")),
    ]
    assert sum(_units(p) for p in fee.postings) == 0


def _document(tmp_path: Path, txns: list[dict[str, object]], **extra: object) -> str:
    path = tmp_path / "stripe.json"
    path.write_text(json.dumps({"data": txns, **extra}))
    return str(path)


CHARGE: dict[str, object] = {
    "id": "txn_1",
    "object": "balance_transaction",
    "type": "charge",
    "currency": "eur",
    "created": 1778803200,
    "amount": 10000,
    "fee": 320,
    "net": 9680,
    "description": "Invoice 1",
}


def test_money_in_an_unmapped_currency_raises(tmp_path: Path) -> None:
    usd = dict(CHARGE, id="txn_usd", currency="usd")
    with pytest.raises(ValueError, match=r"txn_usd.*USD.*account_map"):
        _importer().extract(_document(tmp_path, [CHARGE, usd]), existing=[])


def test_an_unmapped_balance_raises_unless_zero(tmp_path: Path) -> None:
    held = {"as_of": "2026-07-10", "available": [{"amount": 123, "currency": "usd"}]}
    with pytest.raises(ValueError, match=r"balance.*USD.*account_map"):
        _importer().extract(_document(tmp_path, [CHARGE], balance=held), existing=[])


@pytest.mark.parametrize("field", ["id", "created", "net", "fee"])
def test_a_missing_field_raises(tmp_path: Path, field: str) -> None:
    txn = {k: v for k, v in CHARGE.items() if k != field}
    with pytest.raises(ValueError, match=rf"txn_1|#0.*no \w+ '{field}'"):
        _importer().extract(_document(tmp_path, [txn]), existing=[])


def test_without_fees_account_the_counter_leg_is_the_net(tmp_path: Path) -> None:
    """The fee is not posted, so it must not be subtracted either."""
    importer = Importer(ACCOUNTS, payee_rules=((r"invoice", "Income:SaaS", "!"),))
    entries = importer.extract(_document(tmp_path, [CHARGE]), existing=[])
    txn = entries[0]
    assert isinstance(txn, data.Transaction)
    assert [(p.account, _units(p)) for p in txn.postings] == [
        ("Assets:Stripe:EUR", Decimal("96.80")),
        ("Income:SaaS", Decimal("-96.80")),
    ]
