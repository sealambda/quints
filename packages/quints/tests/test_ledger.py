"""Tests for shared ledger helpers."""

from datetime import date as Date
from decimal import Decimal

import pytest

from quints import ledger


def test_vat_rate_by_period():
    assert ledger.vat_rate(Date(2024, 1, 1)) == Decimal("0.081")
    assert ledger.vat_rate(Date(2026, 7, 11)) == Decimal("0.081")
    assert ledger.vat_rate(Date(2023, 12, 31)) == Decimal("0.077")
    assert ledger.vat_rate(Date(2018, 1, 1)) == Decimal("0.077")
    assert ledger.vat_rate(Date(2017, 12, 31)) == Decimal("0.080")
    assert ledger.vat_rate(Date(2011, 1, 1)) == Decimal("0.080")


def test_vat_rate_unknown_before_2011():
    with pytest.raises(ValueError):
        ledger.vat_rate(Date(2010, 12, 31))
