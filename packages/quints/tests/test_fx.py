"""Tests for the year-end FX revaluation helper."""

from decimal import Decimal

from quints import fx

_LEDGER = """
2024-01-01 open Assets:CH:GmbH:Current:Wise:EUR EUR
2024-01-01 open Assets:CH:GmbH:Current:UBS:CHF CHF
2024-01-01 open Income:CH:GmbH:Consulting:External:Export
2024-01-01 open Income:CH:GmbH:FX:CurrencyGain CHF
2024-01-01 open Expenses:CH:GmbH:FX:CurrencyLoss CHF

2026-02-01 price EUR 0.95 CHF

2026-02-05 * "Client" "export received"
    Assets:CH:GmbH:Current:Wise:EUR           200.00 EUR
    Income:CH:GmbH:Consulting:External:Export

2026-12-30 price EUR 0.90 CHF
"""


def _ledger_file(tmp_path):
    f = tmp_path / "main.bean"
    f.write_text(_LEDGER)
    return f


def test_compute_delta_book_vs_market(tmp_path):
    (r,) = fx.compute(_ledger_file(tmp_path), "2026-12-31")
    assert r.currency == "EUR"
    assert r.units == Decimal("200.00")
    assert r.book_chf == Decimal("190.00")    # 200 @ 0.95 (txn-date rate)
    assert r.market_chf == Decimal("180.00")  # 200 @ 0.90 (year-end rate)
    assert r.delta == Decimal("-10.00")


def test_revaluation_text_balances(tmp_path):
    revaluations = fx.compute(_ledger_file(tmp_path), "2026-12-31")
    text = fx.revaluation_text(revaluations, "2026-12-31")
    assert '"Year-end FX revaluation EUR (Art. 960 OR)"' in text
    assert "-200.00 EUR @@ 190.00 CHF" in text
    assert "200.00 EUR @@ 180.00 CHF" in text
    assert "Expenses:CH:GmbH:FX:CurrencyLoss" in text
    assert "10.00 CHF" in text
    # weights: -190 + 180 + 10 = 0 — the transaction balances


def test_booked_text_zeroes_future_revaluation(tmp_path):
    f = tmp_path / "main.bean"
    text = fx.revaluation_text(fx.compute(_ledger_file(tmp_path), "2026-12-31"), "2026-12-31")
    f.write_text(_LEDGER + "\n" + text + "\n")
    revaluations = fx.compute(f, "2026-12-31")
    assert all(r.delta == 0 for r in revaluations)
