"""Invariants of the money arithmetic, the payment references and the rate
tables — stated once, checked on generated cases (hypothesis)."""

from __future__ import annotations

import os
from datetime import date, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from quints import ledger
from quints.invoice import reference as ref
from quints.invoice.model import Invoice, LineItem, Party, SupplyPeriod, VatBlock, compute

_amount = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("99999"), places=2)
_quantity = st.decimals(min_value=Decimal("0.25"), max_value=Decimal("500"), places=2)
_lines = st.lists(st.tuples(_quantity, _amount), min_size=1, max_size=8)
_rate_class = st.sampled_from(sorted(ledger.VAT_RATE_CLASSES))
_day = st.dates(min_value=date(2011, 1, 1), max_value=date(2030, 12, 31))


def _invoice(
    lines: list[tuple[Decimal, Decimal]], on: date, rate_class: str, round_5: bool
) -> Invoice:
    return Invoice(
        number="INV1",
        kind="domestic",
        currency="CHF",
        issue_date=on,
        supply=SupplyPeriod.day(on),
        customer=Party(name="ACME AG", address=["Bahnhofstrasse 1", "8000 Zürich"]),
        items=[LineItem(description="Work", quantity=q, unit_price=p) for q, p in lines],
        vat=VatBlock(rate=ledger.vat_rate(on, rate_class) * 100),
        round_5=round_5,
    )


@given(_lines, _day, _rate_class, st.booleans())
def test_net_plus_vat_plus_rounding_is_the_total(
    lines: list[tuple[Decimal, Decimal]], on: date, rate_class: str, round_5: bool
) -> None:
    t = compute(_invoice(lines, on, rate_class, round_5))
    assert t.subtotal + t.vat_amount + t.rounding == t.grand_total == t.amount_due
    assert t.vat_amount == t.vat_amount.quantize(Decimal("0.01"))


@given(_lines, _day, _rate_class)
def test_five_rappen_rounding_moves_the_total_only(
    lines: list[tuple[Decimal, Decimal]], on: date, rate_class: str
) -> None:
    exact = compute(_invoice(lines, on, rate_class, False))
    rounded = compute(_invoice(lines, on, rate_class, True))
    assert rounded.vat_amount == exact.vat_amount and exact.rounding == 0
    assert abs(rounded.rounding) <= Decimal("0.025")
    assert rounded.grand_total % Decimal("0.05") == 0


@given(_lines, _day, _rate_class, st.randoms())
def test_line_order_never_changes_the_totals(
    lines: list[tuple[Decimal, Decimal]], on: date, rate_class: str, rnd: object
) -> None:
    shuffled = list(lines)
    rnd.shuffle(shuffled)  # type: ignore[attr-defined]
    assert compute(_invoice(shuffled, on, rate_class, True)) == compute(
        _invoice(lines, on, rate_class, True)
    )


_number = st.from_regex(r"[A-Z]{2,4}[0-9]{1,8}", fullmatch=True)
_bank_id = st.none() | st.from_regex(r"[0-9]{6}", fullmatch=True)


@given(_number, _bank_id)
def test_every_reference_validates_and_decodes_to_its_number(
    number: str, bank_id: str | None
) -> None:
    scor = ref.parse_reference(ref.make_scor(number))
    assert scor.kind == "SCOR" and scor.value[4:] == number  # RF + 2 check digits
    qrr = ref.make_qrr(number, bank_id)
    assert ref.parse_reference(qrr).kind == "QRR" and ref.decode_qrr(qrr) == number


@given(_number, _number, _bank_id)
def test_two_numbers_never_share_a_reference(a: str, b: str, bank_id: str | None) -> None:
    if a != b:
        assert ref.make_scor(a) != ref.make_scor(b)
        assert ref.make_qrr(a, bank_id) != ref.make_qrr(b, bank_id)


# ── rate tables ───────────────────────────────────────────────────────────────

_TABLES = {**ledger.VAT_RATE_CLASSES, "saldo": ledger.SALDO_RATES}


def test_rate_periods_are_contiguous() -> None:
    """Each table is a list of validity starts, newest first: a period ends
    where the next begins, so there is neither a gap nor an overlap — as long
    as the starts strictly descend and all classes reach equally far back."""
    for name, table in _TABLES.items():
        starts = [start for start, _ in table]
        assert starts == sorted(set(starts), reverse=True), name
    assert len({table[-1][0] for table in _TABLES.values()}) == 1


def test_every_rate_period_cites_its_source() -> None:
    periods = {(name, start) for name, table in _TABLES.items() for start, _ in table}
    assert periods == set(ledger.RATE_SOURCES)
    for source in ledger.RATE_SOURCES.values():
        assert source.url.startswith("https://") and source.article


@pytest.mark.skipif(not os.environ.get("QUINTS_WEEKLY"), reason="weekly CI run only")
def test_no_source_was_last_read_over_a_year_ago() -> None:
    stale = {
        key: s
        for key, s in ledger.RATE_SOURCES.items()
        if s.checked_on and date.today() - s.checked_on > timedelta(days=365)
    }
    assert not stale, f"re-read these sources and update checked_on: {stale}"
