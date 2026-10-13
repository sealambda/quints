"""The statements cover every account code, and the two results agree.

Art. 959b OR lists the income-statement positions; the KMU Kontenrahmen puts
them in classes 3-8. Codes 7000-8999 used to vanish from the Erfolgsrechnung
while the Bilanz result (a balancing figure) kept them, so the two results
differed with nothing said. Now every class has its row, a code that maps to
no row is a violation, and Bilanz result − Erfolg result must equal the
unrealised FX the Bilanz computes — or the difference is reported.
"""

import json
from decimal import Decimal
from pathlib import Path

from typer.testing import CliRunner

from quints import config, kmu, mwst
from quints.cli import app

runner = CliRunner()
CFG = config.Config()

_OPEN = """\
2026-01-01 open Assets:CH:GmbH:Current:UBS:CHF CHF
  kmu: "1020"
2026-01-01 open Equity:CH:GmbH:Capital CHF
  kmu: "2800"
2026-01-01 open Income:CH:GmbH:Consulting:External:Domestic CHF
  kmu: "3400"
2026-01-01 open Income:CH:GmbH:Rent CHF
  kmu: "7500"
2026-01-01 open Expenses:CH:GmbH:Material CHF
  kmu: "4400"
2026-01-01 open Expenses:CH:GmbH:Donations CHF
  kmu: "8000"
2026-01-01 open Income:CH:GmbH:Windfall CHF
  kmu: "8510"
2026-01-01 open Expenses:CH:GmbH:Tax:Direct CHF
  kmu: "8900"
"""

YEAR = """
2026-01-02 * "Owner" "capital"
  Assets:CH:GmbH:Current:UBS:CHF   20000.00 CHF
  Equity:CH:GmbH:Capital          -20000.00 CHF
2026-03-01 * "Client" "consulting"
  Assets:CH:GmbH:Current:UBS:CHF    1000.00 CHF
  Income:CH:GmbH:Consulting:External:Domestic  -1000.00 CHF
2026-03-02 * "Tenant" "sublet office"
  Assets:CH:GmbH:Current:UBS:CHF    1000.00 CHF
  Income:CH:GmbH:Rent              -1000.00 CHF
2026-03-03 * "Supplier" "material"
  Expenses:CH:GmbH:Material          500.00 CHF
  Assets:CH:GmbH:Current:UBS:CHF    -500.00 CHF
2026-03-04 * "Charity" "donation"
  Expenses:CH:GmbH:Donations         100.00 CHF
  Assets:CH:GmbH:Current:UBS:CHF    -100.00 CHF
2026-03-05 * "Insurer" "one-off refund"
  Assets:CH:GmbH:Current:UBS:CHF     200.00 CHF
  Income:CH:GmbH:Windfall           -200.00 CHF
2026-03-06 * "ESTV" "profit tax"
  Expenses:CH:GmbH:Tax:Direct        300.00 CHF
  Assets:CH:GmbH:Current:UBS:CHF    -300.00 CHF
"""


def _write(tmp_path: Path, body: str, opens: str = _OPEN) -> Path:
    led = tmp_path / "main.bean"
    led.write_text(opens + body)
    return led


def _both(led: Path) -> tuple[kmu.BilanzReport, kmu.ErfolgReport]:
    b = kmu.compute_bilanz(led, "2026-12-31", CFG)
    e = kmu.compute_erfolg(led, "2026-01-01", "2026-12-31", CFG)
    return b, e


def test_classes_7_and_8_have_their_art_959b_rows(tmp_path: Path) -> None:
    b, e = _both(_write(tmp_path, YEAR))
    assert e.ebit == Decimal("500.00") and e.ebt == Decimal("500.00")
    others = {r.key: r.amount for r in e.other_results}
    assert others == {
        "ancillary": Decimal("1000.00"),
        "non_operating_expenses": Decimal("-100.00"),
        "extraordinary_income": Decimal("200.00"),
    }
    assert e.result_before_taxes == Decimal("1600.00")
    assert [(r.key, r.amount) for r in e.direct_taxes] == [("direct_taxes", Decimal("300.00"))]
    assert e.result == b.result == Decimal("1300.00")
    assert e.violations == [] and b.violations == []


def test_a_code_in_no_row_is_a_violation(tmp_path: Path) -> None:
    opens = _OPEN + (
        '2026-01-01 open Expenses:CH:GmbH:Odd CHF\n  kmu: "8300"\n'
        '2026-01-01 open Assets:CH:GmbH:Odd CHF\n  kmu: "1950"\n'
    )
    body = YEAR + (
        '2026-04-01 * "x" "odd"\n  Expenses:CH:GmbH:Odd  50.00 CHF\n'
        "  Assets:CH:GmbH:Odd  -50.00 CHF\n"
    )
    b, e = _both(_write(tmp_path, body, opens))
    assert any("8300" in v.message and "no row" in v.message for v in e.violations)
    assert any("1950" in v.message and "no row" in v.message for v in b.violations)


def test_unrealised_fx_explains_the_difference(tmp_path: Path) -> None:
    opens = (
        _OPEN.replace("External:Domestic CHF", "External:Domestic")
        + '2026-01-01 open Assets:CH:GmbH:Current:Wise:EUR EUR\n  kmu: "1020"\n'
    )
    body = """
2026-03-01 price EUR 0.90 CHF
2026-03-01 * "Client" "consulting in EUR"
  Assets:CH:GmbH:Current:Wise:EUR   1000.00 EUR
  Income:CH:GmbH:Consulting:External:Domestic  -1000.00 EUR
2026-12-31 price EUR 0.95 CHF
"""
    b, e = _both(_write(tmp_path, body, opens))
    assert (b.result, e.result, b.unrealized_fx) == (
        Decimal("950.00"),
        Decimal("900.00"),
        Decimal("50.00"),
    )
    assert b.violations == []


def test_an_unexplained_difference_is_a_violation(tmp_path: Path) -> None:
    """An expense account with no kmu: code (the kmu plugin isn't loaded):
    the Bilanz sees the cash leave, the Erfolgsrechnung never sees the cost."""
    opens = _OPEN + "2026-01-01 open Expenses:CH:GmbH:Misc CHF\n"
    body = YEAR + (
        '2026-04-01 * "x" "uncoded"\n  Expenses:CH:GmbH:Misc  70.00 CHF\n'
        "  Assets:CH:GmbH:Current:UBS:CHF  -70.00 CHF\n"
    )
    b, _ = _both(_write(tmp_path, body, opens))
    (v,) = b.violations
    assert "Erfolgsrechnung" in v.message
    assert (v.expected, v.posted) == (Decimal("1300.00"), Decimal("1230.00"))


def test_bilanz_rows_for_art_959a_positions(tmp_path: Path) -> None:
    opens = _OPEN + (
        '2026-01-01 open Assets:CH:GmbH:Participations CHF\n  kmu: "1480"\n'
        '2026-01-01 open Assets:CH:GmbH:Unpaid CHF\n  kmu: "1850"\n'
        '2026-01-01 open Equity:CH:GmbH:OwnShares CHF\n  kmu: "2965"\n'
        '2026-01-01 open Equity:CH:GmbH:CapitalReserve CHF\n  kmu: "2900"\n'
    )
    body = """
2026-01-02 * "Owner" "capital, half paid in"
  Assets:CH:GmbH:Current:UBS:CHF   10000.00 CHF
  Assets:CH:GmbH:Unpaid            10000.00 CHF
  Equity:CH:GmbH:Capital          -20000.00 CHF
2026-02-01 * "Owner" "agio"
  Assets:CH:GmbH:Current:UBS:CHF    1000.00 CHF
  Equity:CH:GmbH:CapitalReserve    -1000.00 CHF
2026-03-01 * "Sub" "stake"
  Assets:CH:GmbH:Participations     4000.00 CHF
  Assets:CH:GmbH:Current:UBS:CHF   -4000.00 CHF
2026-04-01 * "Buyback" "own shares"
  Equity:CH:GmbH:OwnShares           500.00 CHF
  Assets:CH:GmbH:Current:UBS:CHF    -500.00 CHF
"""
    b, _ = _both(_write(tmp_path, body, opens))
    rows = b.noncurrent_assets + b.equity
    keys = {r.key: r.amount for r in rows + [p for r in rows for p in r.parts]}
    assert keys["participations"] == Decimal("4000.00")
    assert keys["unpaid_capital"] == Decimal("10000.00")
    assert keys["capital_reserve"] == Decimal("1000.00")
    assert keys["own_shares"] == Decimal("-500.00")  # a Minusposten
    assert b.violations == [] and b.result == 0


def test_cli_erfolg_and_bilanz_json(tmp_path: Path) -> None:
    led = _write(tmp_path, YEAR)
    res = runner.invoke(app, ["report", "erfolg", "--year", "2026", "--json", "-f", str(led)])
    assert res.exit_code == 0, res.output
    d = json.loads(res.stdout)
    assert d["result"] == "1300.00" and d["direct_taxes"][0]["key"] == "direct_taxes"
    res = runner.invoke(app, ["report", "erfolg", "--year", "2026", "--lang", "de", "-f", str(led)])
    assert "Betriebsfremder Aufwand" in res.output and "Direkte Steuern" in res.output
    res = runner.invoke(app, ["report", "bilanz", "--at", "2026-12-31", "--json", "-f", str(led)])
    assert json.loads(res.stdout)["unrealized_fx"] == "0.00"


def test_cli_statements_report_an_unexplained_difference(tmp_path: Path) -> None:
    opens = _OPEN + "2026-01-01 open Expenses:CH:GmbH:Misc CHF\n"
    body = YEAR + (
        '2026-04-01 * "x" "uncoded"\n  Expenses:CH:GmbH:Misc  70.00 CHF\n'
        "  Assets:CH:GmbH:Current:UBS:CHF  -70.00 CHF\n"
    )
    led = _write(tmp_path, body, opens)
    out = tmp_path / "s.pdf"
    argv = ["report", "statements", "--year", "2026", "-o", str(out), "-f", str(led)]
    res = runner.invoke(app, argv)
    assert res.exit_code == 0, res.output
    assert out.exists() and "Erfolgsrechnung" in res.output


# ── VAT report: mixed rates and Rappen ─────────────────────────────────────────

_VAT_OPEN = """\
2026-01-01 open Assets:CH:GmbH:Current:UBS:CHF CHF
2026-01-01 open Liabilities:CH:GmbH:Tax:OutputVAT CHF
2026-01-01 open Income:CH:GmbH:Consulting:External:Domestic CHF
2026-01-01 open Income:CH:GmbH:Consulting:External:Export CHF
2026-01-01 open Income:CH:GmbH:Books CHF
"""


def test_mixed_rate_sale_splits_its_tax_by_rate(tmp_path: Path) -> None:
    led = tmp_path / "main.bean"
    led.write_text(
        _VAT_OPEN
        + """
2026-07-02 * "Shop" "mixed sale"
  Assets:CH:GmbH:Current:UBS:CHF   1134.10 CHF
  Income:CH:GmbH:Consulting:External:Domestic   -100.00 CHF
  Income:CH:GmbH:Books            -1000.00 CHF
    mwst: "reduced"
  Liabilities:CH:GmbH:Tax:OutputVAT  -34.10 CHF
"""
    )
    r = mwst.compute(led, "2026-07-01", "2026-09-30", CFG)
    assert (r.z303_net, r.z303_tax) == (Decimal("100.00"), Decimal("8.10"))
    assert (r.z313_net, r.z313_tax) == (Decimal("1000.00"), Decimal("26.00"))
    assert r.violations == []


def test_ziffern_are_rappen_and_the_form_adds_up(tmp_path: Path) -> None:
    led = tmp_path / "main.bean"
    led.write_text(
        _VAT_OPEN
        + """
2026-07-02 * "Client" "domestic, sub-Rappen net"
  Assets:CH:GmbH:Current:UBS:CHF    108.104 CHF
  Income:CH:GmbH:Consulting:External:Domestic   -100.004 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT   -8.10 CHF
2026-07-03 * "Abroad" "export, sub-Rappen"
  Assets:CH:GmbH:Current:UBS:CHF     50.004 CHF
  Income:CH:GmbH:Consulting:External:Export  -50.004 CHF
"""
    )
    res = runner.invoke(app, ["vat", "report", "-p", "2026-Q3", "--json", "-f", str(led)])
    assert res.exit_code == 0, res.output
    d = json.loads(res.stdout)
    assert (d["z200"], d["z221"], d["z289"], d["z299"]) == ("150.01", "50.00", "50.00", "100.01")
    assert d["z303_net"] == d["z299"]  # the Rappen residual lands on the rate row
    assert Decimal(d["z500"]) == Decimal(d["z399"]) - Decimal(d["z479"])
    assert d["violations"] == []
    text = runner.invoke(app, ["vat", "report", "-p", "2026-Q3", "-f", str(led)]).output
    assert "150.01" in text and "100.01" in text


def test_beancount_balancing_tolerance_is_no_gap(tmp_path: Path) -> None:
    """beancount accepts `100 EUR @ 0.93127 CHF` against 93.13 CHF: each
    conversion leaves up to half a Rappen in the Bilanz the Erfolgsrechnung
    never sees. Forty of them must not read as an unexplained gap."""
    opens = _OPEN + '2026-01-01 open Assets:CH:GmbH:Current:Wise:EUR EUR\n  kmu: "1020"\n'
    body = "".join(
        f'2026-03-{1 + i % 28:02d} * "Client" "EUR sale {i}"\n'
        "  Assets:CH:GmbH:Current:Wise:EUR   100.00 EUR @ 0.93127 CHF\n"
        "  Income:CH:GmbH:Consulting:External:Domestic  -93.13 CHF\n"
        for i in range(40)
    )
    b, e = _both(_write(tmp_path, body + "2026-12-31 price EUR 0.93127 CHF\n", opens))
    assert (b.result, e.result) == (Decimal("3725.08"), Decimal("3725.20"))
    assert b.violations == []


def test_bilanz_names_the_erfolg_code_in_no_row(tmp_path: Path) -> None:
    opens = _OPEN + '2026-01-01 open Expenses:CH:GmbH:Odd CHF\n  kmu: "8300"\n'
    body = YEAR + (
        '2026-04-01 * "x" "odd"\n  Expenses:CH:GmbH:Odd  50.00 CHF\n'
        "  Assets:CH:GmbH:Current:UBS:CHF  -50.00 CHF\n"
    )
    b, _ = _both(_write(tmp_path, body, opens))
    assert any("8300" in v.message for v in b.violations)
    assert any("Erfolgsrechnung result" in v.message for v in b.violations)


RESERVES_OPEN = _OPEN + "".join(
    f'2026-01-01 open Equity:CH:GmbH:R{code} CHF\n  kmu: "{code}"\n'
    for code in ("2900", "2950", "2955", "2960", "2965")
)
RESERVES = """
2026-01-02 * "Owner" "capital"
  Assets:CH:GmbH:Current:UBS:CHF   20000.00 CHF
  Equity:CH:GmbH:Capital          -20000.00 CHF
2026-02-01 * "Reserves" "booked"
  Assets:CH:GmbH:Current:UBS:CHF    1500.00 CHF
  Equity:CH:GmbH:R2900             -1000.00 CHF
  Equity:CH:GmbH:R2950              -200.00 CHF
  Equity:CH:GmbH:R2955               -50.00 CHF
  Equity:CH:GmbH:R2960              -250.00 CHF
2026-03-01 * "Buyback" "own shares"
  Equity:CH:GmbH:R2965               500.00 CHF
  Assets:CH:GmbH:Current:UBS:CHF    -500.00 CHF
"""


def test_reserves_keeps_its_old_figure_as_a_subtotal(tmp_path: Path) -> None:
    """`reserves` (2900-2969, own shares inside) is a stable JSON key: it
    stays, with the Art. 959a positions as its parts, counted once."""
    led = _write(tmp_path, RESERVES, RESERVES_OPEN)
    b, _ = _both(led)
    (reserves,) = [r for r in b.equity if r.key == "reserves"]
    assert reserves.amount == Decimal("1000.00")  # what the single row showed before
    assert [(p.key, p.amount) for p in reserves.parts] == [
        ("capital_reserve", Decimal("1000.00")),
        ("retained_reserve", Decimal("200.00")),
        ("revaluation_reserve", Decimal("50.00")),
        ("voluntary_reserves", Decimal("250.00")),
        ("own_shares", Decimal("-500.00")),
    ]
    assert b.total_liabilities_equity == b.total_assets == Decimal("21000.00")
    assert b.result == 0 and b.violations == []
    res = runner.invoke(app, ["report", "bilanz", "--at", "2026-12-31", "--json", "-f", str(led)])
    equity = {r["key"]: r["amount"] for r in json.loads(res.stdout)["equity"]}
    assert equity == {"share_capital": "20000.00", "reserves": "1000.00"}
    text = runner.invoke(app, ["report", "bilanz", "--at", "2026-12-31", "-f", str(led)]).output
    assert text.count("Reserves") == 1 and "Own shares" in text and "-500.00" in text
