"""Tests for the Stripe balance-transaction importer."""

from datetime import date as Date
from decimal import Decimal
from pathlib import Path

from beancount.core import data

from beangulp_stripe import Importer, major_units

HERE = Path(__file__).parent
FIXTURE = str(HERE / "fixture-balance-transactions.json")

ACCOUNTS = {"EUR": "Assets:Stripe:EUR"}
RULES = (
    (r"payout", "Assets:Transfer:Stripe", "*"),
    (r"acme|beta", "Income:SaaS", "!"),
)


def _importer(**kwargs):
    kwargs.setdefault("fees_account", "Expenses:Fees")
    kwargs.setdefault("tax_account", "Assets:Tax:InputVAT")
    kwargs.setdefault("payee_rules", RULES)
    return Importer(ACCOUNTS, **kwargs)


def test_major_units_handles_currency_exponents():
    assert major_units(14900, "eur") == Decimal("149.00")
    assert major_units(-621, "EUR") == Decimal("-6.21")
    assert major_units(500, "jpy") == Decimal("500")
    assert major_units(1250, "kwd") == Decimal("1.250")


def test_identify_by_schema_currency_and_account(tmp_path):
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


def test_extract_charge_fee_payment_payout_and_balance():
    entries = _importer().extract(FIXTURE, existing=[])
    txns = [e for e in entries if isinstance(e, data.Transaction)]
    balances = [e for e in entries if isinstance(e, data.Balance)]
    assert len(txns) == 4  # zero adjustment and unmapped USD charge skipped
    assert len(balances) == 1

    charge, monthly_fee, payment, payout = txns

    # Per-charge processing fee split out; income counter leg is the gross.
    assert charge.date == Date(2026, 5, 15)
    assert charge.payee == "ACME Labs GmbH"
    assert charge.flag == "!"
    assert charge.meta["stripe_id"] == "txn_charge_may"
    assert [(p.account, p.units.number) for p in charge.postings] == [
        ("Assets:Stripe:EUR", Decimal("143.91")),
        ("Expenses:Fees", Decimal("5.09")),
        ("Income:SaaS", Decimal("-149.00")),
    ]

    # Separately billed usage fee: VAT fee_detail → tax account, counter leg
    # net of tax, transaction balances, still review-flagged.
    assert monthly_fee.date == Date(2026, 5, 31)
    assert monthly_fee.payee == "Stripe"
    assert monthly_fee.flag == "!"
    assert [(p.account, p.units.number) for p in monthly_fee.postings] == [
        ("Assets:Stripe:EUR", Decimal("-1.12")),
        ("Assets:Tax:InputVAT", Decimal("0.08")),
        ("Expenses:Fees", Decimal("1.04")),
    ]
    assert sum(p.units.number for p in monthly_fee.postings) == 0

    # Per-transaction fee split: net cash, explicit fee, gross counter leg.
    assert payment.payee == "Beta AG"
    assert [(p.account, p.units.number) for p in payment.postings] == [
        ("Assets:Stripe:EUR", Decimal("96.80")),
        ("Expenses:Fees", Decimal("3.20")),
        ("Income:SaaS", Decimal("-100.00")),
    ]

    assert payout.payee == "Stripe"
    assert payout.flag == "*"
    assert [(p.account, p.units.number) for p in payout.postings] == [
        ("Assets:Stripe:EUR", Decimal("-200.00")),
        ("Assets:Transfer:Stripe", Decimal("200.00")),
    ]

    # available + pending for mapped currencies only, dated as_of + 1.
    assert balances[0].date == Date(2026, 7, 11)
    assert balances[0].account == "Assets:Stripe:EUR"
    assert balances[0].amount.number == Decimal("286.58")


def test_extract_skips_ids_already_in_the_ledger():
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


def test_without_rules_drafts_keep_cash_and_fee_legs_only():
    entries = Importer(ACCOUNTS, fees_account="Expenses:Fees").extract(FIXTURE, existing=[])
    charge = next(
        e
        for e in entries
        if isinstance(e, data.Transaction) and e.meta["stripe_id"] == "txn_charge_may"
    )
    assert charge.flag == "!"
    assert [p.account for p in charge.postings] == ["Assets:Stripe:EUR", "Expenses:Fees"]


def test_without_tax_account_tax_folds_into_fees_but_still_balances():
    entries = Importer(ACCOUNTS, fees_account="Expenses:Fees").extract(FIXTURE, existing=[])
    fee = next(
        e
        for e in entries
        if isinstance(e, data.Transaction) and e.meta["stripe_id"] == "txn_fee_may"
    )
    assert [(p.account, p.units.number) for p in fee.postings] == [
        ("Assets:Stripe:EUR", Decimal("-1.12")),
        ("Expenses:Fees", Decimal("0.08")),
        ("Expenses:Fees", Decimal("1.04")),
    ]
    assert sum(p.units.number for p in fee.postings) == 0
