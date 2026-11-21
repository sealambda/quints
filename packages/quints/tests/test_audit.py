"""`quints check` is the one gate: it sees what would make a statement or a
VAT return wrong, not only what beancount rejects.

Before, a code in no statutory row passed `check` and surfaced only when the
statements were built. Now `check` runs the statements' identity and every
VAT return not settled yet, over one load, and locates each problem.
"""

import json
from datetime import date as Date
from pathlib import Path

from typer.testing import CliRunner

from quints import audit, closing, config, kmu, ledger, mwst
from quints.cli import app

runner = CliRunner()

CHART = """\
2026-01-01 open Assets:CH:GmbH:Current:UBS:CHF CHF
  kmu: "1020"
2026-01-01 open Equity:CH:GmbH:Capital CHF
  kmu: "2800"
2026-01-01 open Liabilities:CH:GmbH:Tax:OutputVAT CHF
  kmu: "2200"
2026-01-01 open Liabilities:CH:GmbH:Tax:PayableVAT CHF
  kmu: "2201"
2026-01-01 open Income:CH:GmbH:Consulting:External:Domestic CHF
  kmu: "3400"
2026-01-01 open Expenses:CH:GmbH:Material CHF
  kmu: "4400"
"""
CAPITAL = """
2026-01-02 * "Owner" "capital"
  Assets:CH:GmbH:Current:UBS:CHF   20000.00 CHF
  Equity:CH:GmbH:Capital          -20000.00 CHF
"""
NOT_REGISTERED = config.Config(vat_registered=False)


def _write(tmp_path: Path, text: str) -> Path:
    led = tmp_path / "main.bean"
    led.write_text(text)
    return led


def _check(led: Path) -> tuple[int, list[dict[str, object]]]:
    (led.parent / "quints.toml").write_text("[entity]\nvat_registered = false\n")
    res = runner.invoke(app, ["check", "--json", "-f", str(led)])
    return res.exit_code, json.loads(res.stdout)["errors"]


def test_demo_unbalanced_and_unopened_fail_located(tmp_path: Path) -> None:
    led = _write(
        tmp_path,
        CHART
        + CAPITAL  # lines 13-16
        + '2026-03-01 * "Supplier" "material"\n'  # line 17: does not balance
        + "  Expenses:CH:GmbH:Material   500.00 CHF\n"
        + "  Assets:CH:GmbH:Current:UBS:CHF  -450.00 CHF\n"
        + '2026-03-02 * "Bank" "fee"\n'  # line 20: an account never opened
        + "  Expenses:CH:GmbH:BankFees   5.00 CHF\n"
        + "  Assets:CH:GmbH:Current:UBS:CHF  -5.00 CHF\n",
    )
    code, errors = _check(led)
    assert code == 1
    ledger_errors = sorted((e["line"], e["file"]) for e in errors if e["code"] == "ledger")
    assert ledger_errors == [(17, str(led)), (20, str(led))]


def test_demo_code_in_no_row_fails_check(tmp_path: Path) -> None:
    """It used to pass `check`: the code is valid KMU, just in no statutory row."""
    led = _write(
        tmp_path,
        CHART
        + '2026-01-01 open Expenses:CH:GmbH:Odd CHF\n  kmu: "8300"\n'  # line 13
        + CAPITAL
        + '2026-04-01 * "x" "odd"\n'
        + "  Expenses:CH:GmbH:Odd  50.00 CHF\n"
        + "  Assets:CH:GmbH:Current:UBS:CHF  -50.00 CHF\n",
    )
    code, errors = _check(led)
    assert code == 1
    assert [(e["code"], e["line"]) for e in errors] == [
        ("kmu-unmapped", 13),
        ("statements-mismatch", None),
    ]


def test_an_account_is_checked_against_the_statement_that_holds_it(tmp_path: Path) -> None:
    # 3400 is an Erfolg code, but on an asset account the Bilanz has no row for it.
    led = _write(
        tmp_path,
        CHART
        + '2026-01-01 open Assets:CH:GmbH:Odd CHF\n  kmu: "3400"\n'
        + CAPITAL.replace("Current:UBS:CHF", "Odd"),
    )
    entries, _ = ledger.load(led)
    (v,) = kmu.unmapped_accounts(entries, ":CH:GmbH:")
    assert (v.code, v.line) == ("kmu-unmapped", 13) and "Bilanz" in v.message


def test_reports_sharing_a_load_keep_their_own_problems(tmp_path: Path) -> None:
    led = _write(tmp_path, 'include "missing.bean"\n' + CHART + CAPITAL)
    loaded = ledger.load(led)
    before = list(loaded[1])
    b = kmu.compute_bilanz(led, "2026-12-31", NOT_REGISTERED, loaded=loaded)
    b.violations.append(ledger.Violation("", "", "", "added by the Bilanz", code="x-test"))
    e = kmu.compute_erfolg(led, "2026-01-01", "2026-12-31", NOT_REGISTERED, loaded=loaded)
    assert loaded[1] == before and e.violations == before  # neither sees the other's


VAT_CFG = config.Config(vat_registered_since=Date(2026, 1, 1))


def _sale(on: str) -> str:
    # 70.00 where net × 8.1 % is 81.00
    return (
        f'{on} * "Acme AG" "Consulting"\n'
        "  Assets:CH:GmbH:Current:UBS:CHF                 1070.00 CHF\n"
        "  Income:CH:GmbH:Consulting:External:Domestic   -1000.00 CHF\n"
        "  Liabilities:CH:GmbH:Tax:OutputVAT               -70.00 CHF\n"
    )


SETTLE_Q1 = """
2026-03-31 * "ESTV" "2026-Q1 VAT settlement" ^VAT-2026-Q1
  Liabilities:CH:GmbH:Tax:OutputVAT    70.00 CHF
  Liabilities:CH:GmbH:Tax:PayableVAT  -70.00 CHF
"""


def test_a_settled_period_does_not_fail_but_its_report_still_lists_it(tmp_path: Path) -> None:
    led = _write(tmp_path, CHART + CAPITAL + _sale("2026-02-02") + SETTLE_Q1)
    assert audit.audit(led, VAT_CFG) == []
    q1 = mwst.compute(led, "2026-01-01", "2026-03-31", VAT_CFG)
    assert [v.code for v in q1.violations] == ["vat-mismatch"]


def test_the_same_problem_in_an_open_period_fails(tmp_path: Path) -> None:
    led = _write(tmp_path, CHART + CAPITAL + _sale("2026-02-02") + SETTLE_Q1 + _sale("2026-04-02"))
    found = audit.audit(led, VAT_CFG)
    assert [(v.code, v.date, v.line) for v in found] == [("vat-mismatch", "2026-04-02", 25)]


USD = """
2026-01-01 open Assets:US:LLC:Bank USD
2026-01-01 open Equity:US:LLC:Capital USD
2026-03-01 * "Owner" "LLC capital"
  Assets:US:LLC:Bank       1000.00 USD
  Equity:US:LLC:Capital   -1000.00 USD
"""


OURS = """
2026-01-01 open Assets:CH:GmbH:Current:Wise:USD
  kmu: "1020"
2026-01-01 open Income:CH:GmbH:Consulting:External:Export
  kmu: "3400"
2026-03-01 * "Client" "USD fee"
  Assets:CH:GmbH:Current:Wise:USD              1000.00 USD
  Income:CH:GmbH:Consulting:External:Export   -1000.00 USD
"""


def test_only_the_entity_needs_a_rate(tmp_path: Path) -> None:
    led = _write(tmp_path, CHART + CAPITAL + USD)
    assert audit.audit(led, NOT_REGISTERED) == []  # another entity's USD is not ours
    led = _write(tmp_path, CHART + CAPITAL + OURS)
    found = audit.audit(led, NOT_REGISTERED)
    located = [(v.code, v.line) for v in found if v.file]
    assert located == [("rate-missing", 22), ("rate-missing", 22)]  # each leg, once
    assert all(v.code == "rate-missing" for v in found)  # plus the report-date balance


def test_close_check_reports_what_check_reports(tmp_path: Path) -> None:
    led = _write(
        tmp_path,
        CHART
        + '2026-01-01 open Expenses:CH:GmbH:Odd CHF\n  kmu: "8300"\n'
        + CAPITAL
        + '2026-04-01 * "x" "odd"\n  Expenses:CH:GmbH:Odd  50.00 CHF\n'
        + "  Assets:CH:GmbH:Current:UBS:CHF  -50.00 CHF\n",
    )
    checklist = closing.check(led, 2026, NOT_REGISTERED)
    (item,) = [i for i in checklist.items if i.id == "ledger"]
    assert item.status == closing.FAIL and item.data["errors"] == 2
    assert checklist.violations == audit.audit(led, NOT_REGISTERED)
