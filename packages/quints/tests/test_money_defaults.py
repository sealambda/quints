"""No silent money defaults: what quints can't value is reported, never a 0.

Each case used to land a wrong figure in a return or statement without a
word — an ignored credit note, a missing FX rate valued at CHF 0, a EUR VAT
leg counted as CHF, a VAT rate nobody checked.
"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quints import config, kmu, ledger, liability, match, mwst, payables, receivables, settlement
from quints.cli import app
from quints.invoice import qr
from quints.invoice.model import (
    BankAccount,
    Invoice,
    Issuer,
    LineItem,
    Party,
    SupplyPeriod,
    VatBlock,
    compute,
)

runner = CliRunner()
CFG = config.Config()

_CHART = """\
2026-01-01 open Assets:CH:GmbH:Current:UBS:CHF CHF
  kmu: "1020"
2026-01-01 open Assets:CH:GmbH:Current:Wise:USD USD
  kmu: "1020"
2026-01-01 open Assets:CH:GmbH:Tax:InputVAT CHF
  kmu: "1170"
2026-01-01 open Liabilities:CH:GmbH:Tax:OutputVAT CHF
  kmu: "2200"
2026-01-01 open Liabilities:CH:GmbH:Tax:Bezugsteuer CHF
  kmu: "2200"
2026-01-01 open Income:CH:GmbH:Consulting:External:Domestic CHF
  kmu: "3400"
2026-01-01 open Income:CH:GmbH:Consulting:External:Export
  kmu: "3400"
2026-01-01 open Expenses:CH:GmbH:Material
  kmu: "4400"
2026-01-01 open Expenses:CH:GmbH:IT:Hosting
  kmu: "6570"
2026-01-01 open Expenses:CH:GmbH:Admin:Taxes CHF
  kmu: "6500"
"""


def _write(tmp_path: Path, body: str) -> Path:
    led = tmp_path / "main.bean"
    led.write_text(_CHART + body)
    return led


def _q3(led: Path) -> mwst.MwstReport:
    return mwst.compute(led, "2026-07-01", "2026-09-30", CFG)


# ── a. a supplier credit note reduces input VAT (Art. 41 Abs. 2 MWSTG) ─────────

BILL_AND_CREDIT = """
2026-07-04 * "Supplier" "bill"
  Expenses:CH:GmbH:Material           1000.00 CHF
  Assets:CH:GmbH:Tax:InputVAT           81.00 CHF
  Assets:CH:GmbH:Current:UBS:CHF     -1081.00 CHF

2026-07-05 * "Supplier" "credit note"
  Expenses:CH:GmbH:Material           -500.00 CHF
  Assets:CH:GmbH:Tax:InputVAT          -40.50 CHF
  Assets:CH:GmbH:Current:UBS:CHF       540.50 CHF
"""


def test_supplier_credit_note_reduces_input_vat(tmp_path: Path) -> None:
    r = _q3(_write(tmp_path, BILL_AND_CREDIT))
    assert r.z400 == Decimal("40.50") and r.z479 == Decimal("40.50")
    assert r.violations == []


def test_untagged_input_vat_reclassification_is_a_violation(tmp_path: Path) -> None:
    """InputVAT moved into cost without a credit note: a Vorsteuerkorrektur
    (415) or a pre-liability reversal — quints can't tell, so it asks."""
    r = _q3(
        _write(
            tmp_path,
            BILL_AND_CREDIT.split("\n2026-07-05")[0]
            + """
2026-07-06 * "VAT transition" "Reverse InputVAT"
  Expenses:CH:GmbH:Admin:Taxes          30.00 CHF
  Assets:CH:GmbH:Tax:InputVAT          -30.00 CHF
""",
        )
    )
    assert r.z400 == Decimal("81.00") and r.z415 == 0
    (v,) = r.violations
    assert "vorsteuerkorrektur" in v.message and v.posted == Decimal("-30.00")


# ── b. a missing FX rate is a violation, not CHF 0 ─────────────────────────────

USD_EXPORT = """
2026-07-06 * "US client" "export, no USD rate in the books"
  Assets:CH:GmbH:Current:Wise:USD      5000.00 USD
  Income:CH:GmbH:Consulting:External:Export  -5000.00 USD
"""


def test_missing_rate_is_a_violation_in_the_vat_report(tmp_path: Path) -> None:
    r = _q3(_write(tmp_path, USD_EXPORT))
    assert r.z200 == 0
    assert any("no USD→CHF rate" in v.message for v in r.violations)


def test_missing_rate_is_a_violation_in_the_statements(tmp_path: Path) -> None:
    led = _write(tmp_path, USD_EXPORT)
    e = kmu.compute_erfolg(led, "2026-01-01", "2026-12-31", CFG)
    b = kmu.compute_bilanz(led, "2026-12-31", CFG)
    assert any("no USD→CHF rate" in v.message for v in e.violations)
    assert any("no USD→CHF rate" in v.message for v in b.violations)


def test_missing_rate_is_a_violation_in_the_liability_check(tmp_path: Path) -> None:
    res = liability.compute(_write(tmp_path, USD_EXPORT), date(2026, 12, 31), CFG)
    assert any("no USD→CHF rate" in v.message for v in res.violations)


def test_cli_vat_report_lists_the_missing_rate(tmp_path: Path) -> None:
    led = _write(tmp_path, USD_EXPORT)
    res = runner.invoke(app, ["vat", "report", "-p", "2026-Q3", "--json", "-f", str(led)])
    assert res.exit_code == 0, res.output
    (v,) = json.loads(res.stdout)["violations"]
    assert "no USD→CHF rate" in v["message"]


# ── c. a VAT leg in a foreign currency is converted, and flagged ───────────────

EUR_VAT_LEG = """
2026-01-01 open Assets:CH:GmbH:Current:Wise:EUR EUR
  kmu: "1020"
2026-07-01 price EUR 0.93 CHF
2026-07-04 * "Supplier" "EUR bill, VAT leg in EUR"
  Expenses:CH:GmbH:Material            1000.00 EUR
  Assets:CH:GmbH:Tax:InputVAT            81.00 EUR
  Assets:CH:GmbH:Current:Wise:EUR     -1081.00 EUR
"""


def test_foreign_currency_vat_leg_is_converted_and_flagged(tmp_path: Path) -> None:
    led = tmp_path / "main.bean"
    led.write_text(
        _CHART.replace("open Assets:CH:GmbH:Tax:InputVAT CHF", "open Assets:CH:GmbH:Tax:InputVAT")
        + EUR_VAT_LEG
    )
    r = _q3(led)
    assert r.z400 == Decimal("75.33")  # 81.00 EUR at 0.93, not 81.00 "CHF"
    (v,) = r.violations
    assert "EUR" in v.message and "vat convert" in v.message


# ── d. Bezugsteuer: old-rate net, and a reversal outside a settlement ──────────


def test_old_rate_bezugsteuer_net_uses_the_old_rate(tmp_path: Path) -> None:
    r = _q3(
        _write(
            tmp_path,
            """
2026-07-04 * "Foreign SaaS" "2023 service, invoiced late"
  mwst: "old_rate"
  Expenses:CH:GmbH:IT:Hosting           100.00 CHF
  Assets:CH:GmbH:Tax:InputVAT             7.70 CHF
  Liabilities:CH:GmbH:Tax:Bezugsteuer    -7.70 CHF
  Assets:CH:GmbH:Current:UBS:CHF       -100.00 CHF
""",
        )
    )
    assert (r.z382_net, r.z382_tax) == (Decimal("100.00"), Decimal("7.70"))


def test_bezugsteuer_reversal_reduces_383(tmp_path: Path) -> None:
    r = _q3(
        _write(
            tmp_path,
            """
2026-07-04 * "Foreign SaaS" "reverse charge"
  Expenses:CH:GmbH:IT:Hosting           100.00 CHF
  Assets:CH:GmbH:Tax:InputVAT             8.10 CHF
  Liabilities:CH:GmbH:Tax:Bezugsteuer    -8.10 CHF
  Assets:CH:GmbH:Current:UBS:CHF       -100.00 CHF

2026-07-05 * "Foreign SaaS" "credit note, half refunded"
  Expenses:CH:GmbH:IT:Hosting           -50.00 CHF
  Assets:CH:GmbH:Tax:InputVAT            -4.05 CHF
  Liabilities:CH:GmbH:Tax:Bezugsteuer     4.05 CHF
  Assets:CH:GmbH:Current:UBS:CHF         50.00 CHF
""",
        )
    )
    assert (r.z383_net, r.z383_tax) == (Decimal("50.00"), Decimal("4.05"))
    assert r.z405 == Decimal("4.05") and r.z500 == 0


# ── e. receivables reconcile with the Receivable account ───────────────────────


def test_receivable_without_an_invoice_id_is_reported(tmp_path: Path) -> None:
    led = tmp_path / "main.bean"
    led.write_text(
        """
2024-01-01 open Assets:CH:GmbH:Receivable:Trade
2024-01-01 open Equity:CH:GmbH:Opening
2026-01-01 * "Opening" "receivables carried in"
  Assets:CH:GmbH:Receivable:Trade        300.00 CHF
  Equity:CH:GmbH:Opening                -300.00 CHF
"""
    )
    entries, _ = ledger.load(led)
    assert receivables.compute_from_entries(entries, date(2026, 7, 1), CFG) == []
    (v,) = receivables.reconcile(entries, date(2026, 7, 1), CFG)
    assert "300.00 CHF" in v.message and v.date == "2026-01-01"

    res = runner.invoke(app, ["receivables", "--at", "2026-07-01", "--json", "-f", str(led)])
    assert res.exit_code == 0, res.output
    assert "no invoice" in json.dumps(json.loads(res.stdout)["violations"])


# ── f. the invoice's VAT rate is the law's, for the supply date (Art. 27 MWSTG) ─


def _invoice(supply: SupplyPeriod, vat: VatBlock | None = None) -> Invoice:
    return Invoice(
        number="ACME202306",
        kind="domestic",
        currency="CHF",
        issue_date=date(2024, 2, 1),
        supply=supply,
        customer=Party(name="ACME AG", address=["Bahnhofstrasse 1", "8000 Zürich"]),
        items=[LineItem(description="Consulting", quantity=Decimal(1), unit_price=Decimal(1000))],
        vat=vat or VatBlock(),
    )


def test_invoice_rate_defaults_to_the_rate_on_the_supply_date() -> None:
    assert compute(_invoice(SupplyPeriod.month(2023, 12))).vat_rate == Decimal("7.7")
    assert compute(_invoice(SupplyPeriod.month(2024, 1))).vat_rate == Decimal("8.1")
    reduced = _invoice(SupplyPeriod.month(2024, 1), VatBlock(rate=Decimal("2.6")))
    assert compute(reduced).vat_amount == Decimal("26.00")


@pytest.mark.parametrize(
    ("supply", "rate"),
    [(SupplyPeriod.month(2023, 12), "8.1"), (SupplyPeriod.month(2026, 6), "9")],
)
def test_invoice_rate_not_in_force_is_refused(supply: SupplyPeriod, rate: str) -> None:
    with pytest.raises(ValueError, match="not a Swiss VAT rate"):
        _invoice(supply, VatBlock(rate=Decimal(rate)))


def test_invoice_supply_across_a_rate_change_needs_splitting() -> None:
    period = SupplyPeriod(start=date(2023, 12, 1), end=date(2024, 1, 31))
    with pytest.raises(ValueError, match="rate change"):
        _invoice(period)


# ── g. unreadable inputs raise instead of defaulting ───────────────────────────


def test_qr_bill_refuses_an_address_without_postcode_and_town() -> None:
    issuer = Issuer(
        name="Muster GmbH",
        address=["Musterstrasse 1", "3000 Bern"],
        bank={"CHF": BankAccount(iban="CH93 0076 2011 6238 5295 7")},
    )
    inv = _invoice(SupplyPeriod.month(2024, 1))
    inv.customer = Party(name="ACME AG", address=["Bahnhofstrasse 1"])
    with pytest.raises(ValueError, match=r"ACME AG.*postcode"):
        qr.build_bill(inv, issuer, issuer.bank["CHF"], compute(inv))


def test_unreadable_due_is_a_violation(tmp_path: Path) -> None:
    led = tmp_path / "main.bean"
    led.write_text(
        """
2024-01-01 open Liabilities:CH:GmbH:Payable:Trade
2024-01-01 open Liabilities:CH:GmbH:Tax:PayableVAT
2024-01-01 open Expenses:CH:GmbH:IT:Hosting
2026-05-01 * "Host" "bill" ^HOST-1
  due: "end of month"
  Expenses:CH:GmbH:IT:Hosting            100.00 CHF
  Liabilities:CH:GmbH:Payable:Trade     -100.00 CHF
2026-06-30 * "Q2 VAT Settlement" ^VAT-2026-Q2
  due: "soon"
  Liabilities:CH:GmbH:Tax:PayableVAT    -10.00 CHF
  Expenses:CH:GmbH:IT:Hosting            10.00 CHF
"""
    )
    problems: list[ledger.Violation] = []
    (bill,) = payables.compute(led, date(2026, 7, 1), CFG, problems=problems)[0]
    assert (bill.due_date, bill.days_overdue) == (None, None)  # listed, no made-up due
    (liab,), *_ = settlement.outstanding(led, date(2026, 7, 1), CFG, problems=problems)
    assert liab.due is None
    assert [("end of month" in v.message, "soon" in v.message) for v in problems] == [
        (True, False),
        (False, True),
    ]
    for argv in (["payables", "--at", "2026-07-01"], ["vat", "status"]):
        res = runner.invoke(app, [*argv, "--json", "-f", str(led)])
        assert res.exit_code == 0, res.output  # bad books are reported, not refused
        assert "unreadable due" in json.dumps(json.loads(res.stdout)["violations"])


def test_staging_draft_with_a_syntax_error_is_refused(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "2026-07-01-ubs.bean").write_text(
        '2026-07-01 * "Acme" "paid"\n  Assets:CH:GmbH:Current:UBS:CHF  100.00 CH F\n'
    )
    with pytest.raises(ValueError, match=r"2026-07-01-ubs\.bean:2"):
        match.load_staging(staging)


# ── the same, end to end through the CLI ────────────────────────────────────────


def test_cli_vat_report_nets_the_credit_note(tmp_path: Path) -> None:
    led = _write(tmp_path, BILL_AND_CREDIT)
    res = runner.invoke(app, ["vat", "report", "-p", "2026-Q3", "--json", "-f", str(led)])
    assert res.exit_code == 0, res.output
    d = json.loads(res.stdout)
    assert (d["z400"], d["violations"]) == ("40.50", [])


def test_cli_erfolg_lists_the_missing_rate(tmp_path: Path) -> None:
    led = _write(tmp_path, USD_EXPORT)
    res = runner.invoke(app, ["report", "erfolg", "--year", "2026", "-f", str(led)])
    assert res.exit_code == 0, res.output
    assert "no USD→CHF rate" in res.output


def _invoice_files(tmp_path: Path, extra: str = "", address: str = "8001 Zürich") -> list[str]:
    issuer = tmp_path / "issuer.yaml"
    issuer.write_text(
        "name: Muster GmbH\naddress: [Musterstrasse 1, 3000 Bern]\n"
        "vat_id: CHE-267.359.056 MWST\n"
        "bank:\n  CHF:\n    iban: CH93 0076 2011 6238 5295 7\n"
    )
    inv = tmp_path / "inv.yaml"
    inv.write_text(
        "number: INV2026014\nkind: domestic\ncurrency: CHF\nissue_date: 2026-07-02\n"
        f"supply: 2026-06\ncustomer: {{name: Acme AG, address: [Bahnhofstrasse 1, {address}]}}\n"
        "items:\n  - {description: Consulting, quantity: 1, unit_price: 1000.00}\n" + extra
    )
    out = str(tmp_path / "o.pdf")
    return ["invoice", str(inv), "--issuer", str(issuer), "--out", out, "--no-verify"]


def test_cli_invoice_refuses_a_rate_not_in_force(tmp_path: Path) -> None:
    res = runner.invoke(app, _invoice_files(tmp_path, "vat: {rate: 7.7}\n"))
    assert res.exit_code == 1 and "not a Swiss VAT rate" in res.output


def test_cli_invoice_refuses_an_address_without_a_town(tmp_path: Path) -> None:
    res = runner.invoke(app, _invoice_files(tmp_path, address="Zürich"))
    assert res.exit_code == 1 and "postcode and town" in res.output


def test_cli_match_refuses_an_unparseable_draft(tmp_path: Path) -> None:
    led = _write(tmp_path, "")
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "d.bean").write_text('2026-07-01 * "Acme"\n  Assets:X  1 CH F\n')
    res = runner.invoke(app, ["match", "--staging", str(staging), "-f", str(led)])
    assert res.exit_code == 1 and "d.bean:2" in res.output


def test_draft_of_a_eur_domestic_invoice_books_its_vat_in_chf(tmp_path: Path) -> None:
    """quints' own draft must not trip the foreign-VAT-leg check: the
    OutputVAT leg is CHF, carrying the invoice's EUR VAT as its @@ price."""
    issuer = tmp_path / "issuer.yaml"
    issuer.write_text(
        "name: Muster GmbH\naddress: [Musterstrasse 1, 3000 Bern]\n"
        "vat_id: CHE-267.359.056 MWST\n"
        "bank:\n  EUR:\n    iban: CH93 0076 2011 6238 5295 7\n"
    )
    inv = tmp_path / "inv.yaml"
    inv.write_text(
        "number: INV2026015\nkind: domestic\ncurrency: EUR\nissue_date: 2026-07-02\n"
        "supply: 2026-06\ncustomer: {name: Acme AG, address: [Bahnhofstrasse 1, 8001 Zürich]}\n"
        "items:\n  - {description: Consulting, quantity: 1, unit_price: 1000.00}\n"
    )
    led = tmp_path / "main.bean"
    led.write_text(
        "2026-01-01 open Assets:CH:GmbH:Receivable:Trade\n"
        "2026-01-01 open Income:CH:GmbH:Consulting:External:Domestic\n"
        "2026-01-01 open Liabilities:CH:GmbH:Tax:OutputVAT CHF\n"
        "2026-07-01 price EUR 0.93 CHF\n"
    )
    argv = ["invoice", str(inv), "--issuer", str(issuer), "--out", str(tmp_path / "o.pdf")]
    res = runner.invoke(app, [*argv, "--json", "-f", str(led)])
    assert res.exit_code == 0, res.output
    text = json.loads(res.stdout)["ledger_draft"]
    assert "-75.33 CHF @@ 81.00 EUR" in text
    led.write_text(led.read_text() + text.replace("document:", "doc:") + "\n")
    r = _q3(led)
    assert r.violations == [] and r.z303_tax == Decimal("75.33")
    res = runner.invoke(app, [*argv, "--json", "-f", str(led)])
    assert json.loads(res.stdout)["cross_check"]["ok"] is True


BEZUG_OLD = """
2026-07-04 * "Foreign SaaS" "2023 service, invoiced late"
  mwst: "old_rate"
  Expenses:CH:GmbH:IT:Hosting           100.00 CHF
  Assets:CH:GmbH:Tax:InputVAT             7.70 CHF
  Liabilities:CH:GmbH:Tax:Bezugsteuer    -7.70 CHF
  Assets:CH:GmbH:Current:UBS:CHF       -100.00 CHF

2026-07-05 * "Foreign SaaS" "credit note"
  Expenses:CH:GmbH:IT:Hosting           -50.00 CHF
  mwst: "old_rate"
  Assets:CH:GmbH:Tax:InputVAT            -3.85 CHF
  Liabilities:CH:GmbH:Tax:Bezugsteuer     3.85 CHF
  Assets:CH:GmbH:Current:UBS:CHF         50.00 CHF
"""


@pytest.mark.parametrize(
    ("chart_fix", "body", "expected"),
    [
        (False, BEZUG_OLD, {"z382_net": "50.00", "z382_tax": "3.85", "z405": "3.85"}),
        (True, EUR_VAT_LEG, {"z400": "75.33"}),
    ],
    ids=["bezugsteuer", "eur-vat-leg"],
)
def test_cli_vat_report_ziffern(
    tmp_path: Path, chart_fix: bool, body: str, expected: dict[str, str]
) -> None:
    chart = _CHART.replace("InputVAT CHF", "InputVAT") if chart_fix else _CHART
    led = tmp_path / "main.bean"
    led.write_text(chart + body)
    res = runner.invoke(app, ["vat", "report", "-p", "2026-Q3", "--json", "-f", str(led)])
    assert res.exit_code == 0, res.output
    d = json.loads(res.stdout)
    assert {k: d[k] for k in expected} == expected


def test_cli_liability_lists_the_missing_rate(tmp_path: Path) -> None:
    led = _write(tmp_path, USD_EXPORT)
    res = runner.invoke(app, ["vat", "liability", "--at", "2026-12-31", "--json", "-f", str(led)])
    assert res.exit_code == 0, res.output
    (v,) = json.loads(res.stdout)["violations"]
    assert "no USD→CHF rate" in v["message"]


# ── a. the pre-liability reversal, on the day liability starts ─────────────────

REVERSAL = """
2026-01-15 * "Supplier" "bill before registering"
  Expenses:CH:GmbH:Admin:Taxes         887.00 CHF
  Assets:CH:GmbH:Tax:InputVAT           71.82 CHF
  Assets:CH:GmbH:Current:UBS:CHF      -958.82 CHF

{day} * "VAT transition" "Reverse pre-liability InputVAT"
    Expenses:CH:GmbH:Admin:Taxes   71.82 CHF
    Assets:CH:GmbH:Tax:InputVAT   -71.82 CHF
"""


@pytest.mark.parametrize(("day", "flagged"), [("2026-02-02", False), ("2026-02-03", True)])
def test_pre_liability_reversal_on_the_registration_day(
    tmp_path: Path, day: str, flagged: bool
) -> None:
    """Input VAT booked before registering was never deducted; moving it into
    cost on the day liability starts is no correction of any return."""
    cfg = config.Config(vat_registered_since=date(2026, 2, 2))
    led = _write(tmp_path, REVERSAL.format(day=day))
    r = mwst.compute(led, "2026-01-01", "2026-03-31", cfg)
    assert (r.z405, r.z415, r.z479) == (0, 0, 0)
    assert [v.posted for v in r.violations] == ([Decimal("-71.82")] if flagged else [])


# ── 3b. side-business income that carries OutputVAT is Entgelt ─────────────────

SIDE_BUSINESS = """
2026-01-01 open Income:CH:GmbH:Side:Shop CHF
  kmu: "7000"
2026-07-07 * "Walk-in" "ancillary shop sale"
  Assets:CH:GmbH:Current:UBS:CHF       1081.00 CHF
  Income:CH:GmbH:Side:Shop            -1000.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT     -81.00 CHF
"""


def test_side_business_sale_with_output_vat_is_turnover(tmp_path: Path) -> None:
    """Class-7 income is not turnover by its code — but a sale that charged
    VAT is a supply against Entgelt (Art. 18 Abs. 1 MWSTG): its net belongs
    in 200/303 next to its tax, not tax alone on 303."""
    led = _write(tmp_path, SIDE_BUSINESS)
    r = _q3(led)
    assert (r.z200, r.z299, r.z303_net, r.z303_tax) == (
        Decimal("1000.00"),
        Decimal("1000.00"),
        Decimal("1000.00"),
        Decimal("81.00"),
    )
    assert r.violations == []
    res = liability.compute(led, date(2026, 12, 31), CFG)
    assert res.years[-1].turnover == Decimal("1000.00")


def test_side_business_income_beside_regular_turnover_stays_out(tmp_path: Path) -> None:
    """The VAT belongs to the 3400 sale; the 7000 leg next to it stays out,
    and, untagged, is listed for a mwst: tag."""
    led = _write(
        tmp_path,
        """
2026-01-01 open Income:CH:GmbH:Side:Shop CHF
  kmu: "7000"
2026-07-07 * "Client" "consulting, plus a pass-through"
  Assets:CH:GmbH:Current:UBS:CHF       1131.00 CHF
  Income:CH:GmbH:Consulting:External:Domestic  -1000.00 CHF
  Income:CH:GmbH:Side:Shop              -50.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT     -81.00 CHF
""",
    )
    r = _q3(led)
    assert (r.z200, r.z303_net, r.z303_tax) == (
        Decimal("1000.00"),
        Decimal("1000.00"),
        Decimal("81.00"),
    )
    (v,) = r.violations
    assert "7000" in v.message and v.posted == Decimal("50.00")


def test_cli_vat_report_counts_side_business_sale(tmp_path: Path) -> None:
    led = _write(tmp_path, SIDE_BUSINESS)
    res = runner.invoke(app, ["vat", "report", "-p", "2026-Q3", "--json", "-f", str(led)])
    assert res.exit_code == 0, res.output
    d = json.loads(res.stdout)
    assert (d["z200"], d["z303_net"], d["z303_tax"], d["violations"]) == (
        "1000.00",
        "1000.00",
        "81.00",
        [],
    )


# ── 3b. untagged class-7/8 income is listed so Ziffer 200 can be completed ─────

CLASS_7_8 = """
2026-01-01 open Income:CH:GmbH:Rent CHF
  kmu: "7500"
2026-01-01 open Income:CH:GmbH:Windfall CHF
  kmu: "8510"
2026-01-01 open Income:CH:GmbH:FX:Gain CHF
  kmu: "6950"
2026-07-08 * "Tenant" "sublet office, no VAT"
  Assets:CH:GmbH:Current:UBS:CHF        900.00 CHF
  Income:CH:GmbH:Rent                  -900.00 CHF
2026-07-09 * "Insurer" "provision released"
  Assets:CH:GmbH:Current:UBS:CHF        200.00 CHF
  Income:CH:GmbH:Windfall              -200.00 CHF
2026-07-10 * "Bank" "FX gain"
  Assets:CH:GmbH:Current:UBS:CHF         30.00 CHF
  Income:CH:GmbH:FX:Gain                -30.00 CHF
"""


def test_untagged_class_7_8_income_is_listed(tmp_path: Path) -> None:
    r = _q3(_write(tmp_path, CLASS_7_8))
    assert r.z200 == 0  # stays out of the figures, as before
    flagged = sorted((v.message.split()[2], v.posted) for v in r.violations)
    assert flagged == [("7500", Decimal("900.00")), ("8510", Decimal("200.00"))]  # not 6950


def test_tagged_class_7_8_income_is_classified(tmp_path: Path) -> None:
    body = CLASS_7_8.replace(
        "  Income:CH:GmbH:Rent                  -900.00 CHF\n",
        '  Income:CH:GmbH:Rent                  -900.00 CHF\n    mwst: "exempt"\n',
    ).replace(
        "  Income:CH:GmbH:Windfall              -200.00 CHF\n",
        '  Income:CH:GmbH:Windfall              -200.00 CHF\n    mwst: "not_entgelt"\n',
    )
    r = _q3(_write(tmp_path, body))
    assert (r.z200, r.z230, r.z299) == (Decimal("900.00"), Decimal("900.00"), 0)
    assert r.violations == []


def test_cli_vat_report_lists_untagged_class_7_8_income(tmp_path: Path) -> None:
    led = _write(tmp_path, CLASS_7_8)
    res = runner.invoke(app, ["vat", "report", "-p", "2026-Q3", "--json", "-f", str(led)])
    assert res.exit_code == 0, res.output
    d = json.loads(res.stdout)
    assert d["z200"] == "0.00"
    assert sorted(v["posted"] for v in d["violations"]) == ["200.00", "900.00"]
    assert all("mwst:" in v["message"] for v in d["violations"])
