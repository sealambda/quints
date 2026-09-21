"""Tests for pairing invoices with the balance transactions they document."""

import json
from pathlib import Path

from beangulp_stripe import correlate_invoices

HERE = Path(__file__).parent
TRANSACTIONS = json.loads((HERE / "fixture-balance-transactions.json").read_text())["data"]
INVOICES = json.loads((HERE / "fixture-invoices.json").read_text())["data"]


def _charge(**overrides: object) -> dict[str, object]:
    txn: dict[str, object] = {
        "id": "txn_x",
        "object": "balance_transaction",
        "type": "charge",
        "currency": "eur",
        "created": 1_700_000_000,
        "amount": 14900,
        "source": {"id": "ch_x", "object": "charge"},
    }
    return txn | overrides


def _invoice(**overrides: object) -> dict[str, object]:
    invoice: dict[str, object] = {
        "id": "in_x",
        "object": "invoice",
        "number": "ABCD1234-0009",
        "status": "paid",
        "created": 1_700_000_000,
        "total": 14900,
        "currency": "eur",
        "customer_name": "Example Customer",
        "invoice_pdf": "https://pay.stripe.test/invoice/x/pdf",
    }
    return invoice | overrides


def test_pairs_by_shared_id_and_by_amount_and_time() -> None:
    matches, ambiguities = correlate_invoices(TRANSACTIONS, INVOICES)

    assert ambiguities == []
    by_txn = {m.txn_id: m for m in matches}
    assert set(by_txn) == {"txn_charge_may", "txn_payment_jun"}

    # The June invoice names its payment intent in the newer `payments` list,
    # so the pairing is authoritative rather than inferred.
    june = by_txn["txn_payment_jun"]
    assert june.matched_by == "id"
    assert june.invoice_id == "in_00000000000002"
    assert june.number == "ABCD1234-0003"
    assert june.customer == "Beispiel AG"

    # The May invoice has charge, payment_intent and subscription all null —
    # only amount, currency and creation time connect it to the charge.
    may = by_txn["txn_charge_may"]
    assert may.matched_by == "amount+time"
    assert may.number == "ABCD1234-0002"
    assert may.customer == "ACME Labs GmbH"
    assert may.created == 1778803210
    assert may.pdf_url.endswith("/pdf")


def test_charges_without_an_invoice_are_silent() -> None:
    """Payouts, fee debits and unmatched charges are normal, not failures."""
    matches, ambiguities = correlate_invoices(TRANSACTIONS, [])

    assert (matches, ambiguities) == ([], [])


def test_draft_invoice_without_a_pdf_is_ignored() -> None:
    draft = _invoice(id="in_draft", number=None, status="draft", invoice_pdf=None)
    matches, ambiguities = correlate_invoices([_charge()], [draft])

    assert (matches, ambiguities) == ([], [])


def test_two_invoices_on_one_charge_is_ambiguous() -> None:
    twin = _invoice(id="in_twin", number="ABCD1234-0010")
    matches, ambiguities = correlate_invoices([_charge()], [_invoice(), twin])

    assert matches == []
    assert len(ambiguities) == 1
    assert "txn_x matches 2 invoices" in ambiguities[0]
    assert "ABCD1234-0009, ABCD1234-0010" in ambiguities[0]


def test_two_charges_on_one_invoice_is_ambiguous() -> None:
    """Two identical charges seconds apart: reported once, not paired at random."""
    twin = _charge(id="txn_twin", source={"id": "ch_twin"})
    matches, ambiguities = correlate_invoices([_charge(), twin], [_invoice()])

    assert matches == []
    assert len(ambiguities) == 1
    assert "invoice ABCD1234-0009 matches 2 balance transactions" in ambiguities[0]
    assert "txn_x, txn_twin" in ambiguities[0]


def test_shared_id_beats_an_amount_collision() -> None:
    """An id makes the pairing certain even when another invoice looks alike."""
    named = _invoice(id="in_named", number="ABCD1234-0011", charge="ch_x")
    matches, ambiguities = correlate_invoices([_charge()], [_invoice(), named])

    assert ambiguities == []
    assert [(m.invoice_id, m.matched_by) for m in matches] == [("in_named", "id")]


def test_an_invoice_naming_another_charge_never_falls_back_to_the_heuristic() -> None:
    """It settles a charge outside this window; guessing would misfile it."""
    elsewhere = _invoice(charge="ch_outside_this_window")
    matches, ambiguities = correlate_invoices([_charge()], [elsewhere])

    assert (matches, ambiguities) == ([], [])


def test_amount_currency_and_time_all_have_to_agree() -> None:
    charge = _charge()
    for mismatch in (
        {"total": 14901},  # gross amount differs
        {"currency": "usd"},  # same number, other currency
        {"created": 1_700_000_121},  # just outside the tolerance
    ):
        matches, ambiguities = correlate_invoices([charge], [_invoice(**mismatch)])
        assert (matches, ambiguities) == ([], []), mismatch

    # A zero-amount transaction never pairs, however well the rest lines up.
    matches, _ = correlate_invoices([_charge(amount=0)], [_invoice(total=0)])
    assert matches == []
