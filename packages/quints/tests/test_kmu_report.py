"""Golden tests for the KMU statutory statements (Bilanz / Erfolgsrechnung / Konten)."""

import dataclasses
import json
from decimal import Decimal

from quints import kmu

_LEDGER = """
2024-01-01 open Assets:CH:GmbH:Current:UBS:CHF CHF
    kmu: "1020"
2024-01-01 open Assets:CH:GmbH:Current:Wise:EUR EUR
    kmu: "1020"
2024-01-01 open Assets:CH:GmbH:Receivable:Trade
    kmu: "1100"
2024-01-01 open Liabilities:CH:GmbH:Loan:Ivan CHF
    kmu: "2500"
2024-01-01 open Equity:CH:GmbH:Contributions:Ivan CHF
    kmu: "2800"
2024-01-01 open Income:CH:GmbH:Consulting:External:Export
    kmu: "3400"
2024-01-01 open Expenses:CH:GmbH:IT:Hosting
    kmu: "6570"
2024-01-01 open Expenses:CH:GmbH:BankFees:UBS CHF
    kmu: "6940"

2025-06-01 * "Old supplier" "expense before the report year"
    Expenses:CH:GmbH:IT:Hosting                50.00 CHF
    Liabilities:CH:GmbH:Loan:Ivan

2026-01-10 * "Owner" "capital"
    Assets:CH:GmbH:Current:UBS:CHF           1000.00 CHF
    Equity:CH:GmbH:Contributions:Ivan

2026-02-01 price EUR 0.95 CHF

2026-02-05 * "Client" "export invoiced"
    Assets:CH:GmbH:Receivable:Trade           200.00 EUR
    Income:CH:GmbH:Consulting:External:Export

2026-02-10 * "Client" "export received"
    Assets:CH:GmbH:Current:Wise:EUR           200.00 EUR
    Assets:CH:GmbH:Receivable:Trade          -200.00 EUR

2026-03-01 * "Bank" "fee"
    Expenses:CH:GmbH:BankFees:UBS              10.00 CHF
    Assets:CH:GmbH:Current:UBS:CHF

2026-06-01 price EUR 0.90 CHF
"""


def _ledger_file(tmp_path):
    f = tmp_path / "main.bean"
    f.write_text(_LEDGER)
    return f


def test_bilanz_balances_and_splits_result(tmp_path):
    r = kmu.compute_bilanz(_ledger_file(tmp_path), "2026-06-30")
    # cash: 990 CHF + 200 EUR @ 0.90 (report-date rate) = 1170
    assert r.total_assets == Decimal("1170.00")
    assert r.total_liabilities_equity == r.total_assets
    # prior-year expense is Gewinnvortrag, not the year's result
    assert r.retained_prior == Decimal("-50.00")
    # year at txn rates: 190 − 10 = 180; unrealized EUR drop 0.95→0.90 = −10
    assert r.result == Decimal("170.00")
    assert r.converted == {"EUR": Decimal("200.00")}
    cash = r.current_assets[0]
    assert cash.key == "cash" and cash.amount == Decimal("1170.00")
    assert [c.code for c in cash.codes] == ["1020"]


def test_erfolg_flows_at_transaction_rates(tmp_path):
    r = kmu.compute_erfolg(_ledger_file(tmp_path), "2026-01-01", "2026-12-31")
    assert r.revenue[0].amount == Decimal("190.00")  # 200 EUR @ 0.95
    assert r.ebit == Decimal("190.00")
    assert r.financial_expenses[0].codes[0].code == "6940"
    assert r.result == Decimal("180.00")


def test_erfolg_excludes_prior_year(tmp_path):
    r = kmu.compute_erfolg(_ledger_file(tmp_path), "2025-01-01", "2025-12-31")
    assert r.result == Decimal("-50.00")
    assert not r.revenue


def test_konten_lists_all_touched_codes(tmp_path):
    r = kmu.compute_konten(_ledger_file(tmp_path), "2026-01-01", "2026-12-31")
    assert [k.code for k in r.konten] == ["1020", "1100", "2800", "3400", "6940"]
    revenue = next(k for k in r.konten if k.code == "3400")
    assert revenue.flow == Decimal("-190.00")  # natural beancount sign


def test_labels_are_bilingual():
    assert kmu.label("bilanz_title", "de") == "Bilanz"
    assert kmu.label("bilanz_title", "en") == "Balance sheet"
    assert kmu.kmu_name("6570", "de") == "Informatikaufwand"
    assert kmu.kmu_name("6570", "en") == "IT expenses"
    assert kmu.kmu_name("9999", "en") == "9999"  # unknown code degrades, never errors


def test_reports_serialize_to_json(tmp_path):
    r = kmu.compute_bilanz(_ledger_file(tmp_path), "2026-06-30")
    payload = json.loads(json.dumps(dataclasses.asdict(r), default=str))
    assert payload["total_assets"] == "1170.00"
