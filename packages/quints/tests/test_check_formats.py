"""`quints check` speaks each CI runner's format, with repo-relative paths.

The books sit in a subdirectory of the checkout (`books/sub/`), and the
runner names the checkout (`GITHUB_WORKSPACE`): every path must come out
relative to it, or the annotations land nowhere. The exact strings are
pinned here; the CI job only proves the action fails and passes.
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quints import diagnostics, ledger
from quints.cli import app

runner = CliRunner()

OPENS = """\
2026-01-01 open Assets:CH:GmbH:Receivable:Trade CHF
  kmu: "1100"
2026-01-01 open Income:CH:GmbH:Consulting:External:Export CHF
  kmu: "3400"

2026-03-01 * "Acme AG" "Work" ^INV2026001
  Assets:CH:GmbH:Receivable:Trade                100.00 CHF
  Income:CH:GmbH:Consulting:External:Export   -100.00 CHF
"""
# Unbalanced (an error at line 10), and INV2026002 is missing (a warning there).
BROKEN = """
2026-04-01 * "Acme AG" "Work" ^INV2026003
  Assets:CH:GmbH:Receivable:Trade                100.00 CHF
  Income:CH:GmbH:Consulting:External:Export    -90.00 CHF
"""
ERROR = "ledger error: Transaction does not balance: (10.00 CHF)"
WARNING = "1 invoice number(s) missing between INV2026001 and INV2026003"


@pytest.fixture
def books(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    led = tmp_path / "books" / "sub" / "main.bean"
    led.parent.mkdir(parents=True)
    led.write_text(OPENS + BROKEN)
    return led


def _check(led: Path, *args: str) -> tuple[int, str, str]:
    res = runner.invoke(app, ["check", "-f", str(led), *args])
    return res.exit_code, res.stdout, res.stderr


def test_text(books: Path) -> None:
    code, out, err = _check(books)
    assert code == 1 and out == ""
    assert err == (
        f"books/sub/main.bean:10: {ERROR} [ledger]\n"
        '   2026-04-01 * "Acme AG" "Work" ^INV2026003\n'
        "     Assets:CH:GmbH:Receivable:Trade            100.00 CHF\n"
        "     Income:CH:GmbH:Consulting:External:Export  -90.00 CHF\n"
        "\n\n"
        f"books/sub/main.bean:10: warning: {WARNING} [invoice-gap]\n"
        "1 error(s), 1 warning(s).\n"
    )


def test_github(books: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    step_summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step_summary))
    code, out, _ = _check(books, "--output-format", "github")
    assert code == 1
    assert out == (
        f"::error file=books/sub/main.bean,line=10,title=quints ledger::{ERROR}\n"
        "::warning file=books/sub/main.bean,line=10,title=quints invoice-gap::"
        f"{WARNING}\n"
        "quints check failed: 1 error(s), 1 warning(s).\n"
    )
    assert step_summary.read_text() == (
        "\n### quints check: 1 error(s), 1 warning(s)\n\n"
        "| Severity | Location | Code | Message |\n"
        "| --- | --- | --- | --- |\n"
        f"| error | books/sub/main.bean:10 | `ledger` | {ERROR} |\n"
        f"| warning | books/sub/main.bean:10 | `invoice-gap` | {WARNING} |\n"
    )


def test_azure(books: Path) -> None:
    code, out, _ = _check(books, "--output-format", "azure")
    assert code == 1
    assert out == (
        "##vso[task.logissue type=error;sourcepath=books/sub/main.bean;linenumber=10;"
        f"code=ledger]{ERROR}\n"
        "##vso[task.logissue type=warning;sourcepath=books/sub/main.bean;linenumber=10;"
        f"code=invoice-gap]{WARNING}\n"
    )


def test_json(books: Path) -> None:
    code, out, _ = _check(books, "--json")  # the alias
    assert code == 1
    d = json.loads(out)
    assert d["ok"] is False and d["stats"]["by_type"]["Transaction"] == 2
    (error,) = d["errors"]
    (warning,) = d["warnings"]
    assert error == {
        "code": "ledger",
        "severity": "error",
        "file": "books/sub/main.bean",
        "line": 10,
        "date": "2026-04-01",
        "payee": "Acme AG",
        "narration": "Work",
        "message": ERROR,
        "expected": "0",
        "posted": "0",
        "help": f"{diagnostics.DOCS}#ledger",
        "fix": None,
    }
    assert (warning["code"], warning["severity"], warning["line"]) == ("invoice-gap", "warning", 10)


def test_gitlab_code_quality(books: Path, tmp_path: Path) -> None:
    report = tmp_path / "gl-code-quality.json"
    code, out, _ = _check(books, "--output-format", "gitlab", "--output-file", str(report))
    assert code == 1 and out == ""
    issues = json.loads(report.read_text())
    assert [(i["check_name"], i["severity"]) for i in issues] == [
        ("ledger", "major"),
        ("invoice-gap", "minor"),
    ]
    for issue in issues:
        assert isinstance(issue["description"], str) and issue["description"]
        assert len(issue["fingerprint"]) == 64
        assert issue["location"] == {"path": "books/sub/main.bean", "lines": {"begin": 10}}


def test_sarif(books: Path) -> None:
    code, out, _ = _check(books, "--output-format", "sarif")
    assert code == 1
    log = json.loads(out)
    assert log["version"] == "2.1.0" and len(log["runs"]) == 1
    driver = log["runs"][0]["tool"]["driver"]
    assert driver["name"] == "quints" and isinstance(driver["version"], str)
    assert [r["id"] for r in driver["rules"]] == ["invoice-gap", "ledger"]
    assert all(r["helpUri"].endswith(f"#{r['id']}") for r in driver["rules"])
    results = log["runs"][0]["results"]
    assert [(r["ruleId"], r["level"]) for r in results] == [
        ("ledger", "error"),
        ("invoice-gap", "warning"),
    ]
    for r in results:
        assert isinstance(r["message"]["text"], str)
        assert len(r["partialFingerprints"]["quints/v1"]) == 64
        (loc,) = r["locations"]
        assert loc["physicalLocation"] == {
            "artifactLocation": {"uri": "books/sub/main.bean"},
            "region": {"startLine": 10},
        }


def test_fingerprints_ignore_the_line(books: Path, tmp_path: Path) -> None:
    def fingerprints() -> list[str]:
        report = tmp_path / "gl.json"
        _check(books, "--output-format", "gitlab", "--output-file", str(report))
        return [i["fingerprint"] for i in json.loads(report.read_text())]

    before = fingerprints()
    books.write_text("\n\n" + books.read_text())  # the same problems, two lines down
    assert fingerprints() == before


@pytest.mark.parametrize("fmt", ["json", "github", "gitlab", "azure", "sarif"])
def test_clean_books_print_an_empty_result(
    fmt: str, books: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    books.write_text(OPENS)
    monkeypatch.setenv("QUINTS_OUTPUT_FORMAT", fmt)  # the variable picks the format
    code, out, _ = _check(books)
    assert code == 0, out
    if fmt == "json":
        assert (json.loads(out)["errors"], json.loads(out)["warnings"]) == ([], [])
    elif fmt == "gitlab":
        assert json.loads(out) == []
    elif fmt == "sarif":
        assert json.loads(out)["runs"][0]["results"] == []
    elif fmt == "github":
        assert out.startswith("OK — no errors.") and "::" not in out
    else:
        assert out == ""


def test_warnings_fail_only_with_strict(books: Path) -> None:
    books.write_text(OPENS + BROKEN.replace("-90.00", "-100.00"))  # the gap alone
    code, out, err = _check(books)
    assert code == 0 and out.startswith("OK — no errors.")
    assert err == f"books/sub/main.bean:10: warning: {WARNING} [invoice-gap]\n"
    code, out, _ = _check(books, "--strict", "--output-format", "github")
    assert code == 1 and out.endswith("quints check failed: 0 error(s), 1 warning(s).\n")


def test_close_check_annotates_the_ledger_item(books: Path) -> None:
    res = runner.invoke(
        app, ["close", "check", "--year", "2026", "--output-format", "github", "-f", str(books)]
    )
    assert res.exit_code == 0  # a to-do list: only --strict fails it
    annotation = f"::error file=books/sub/main.bean,line=10,title=quints ledger::{ERROR}\n"
    assert annotation in res.stdout and "Year-end close" in res.stdout
    assert res.stdout.rstrip().splitlines()[-1].startswith("quints close check 2026:")


def test_workflow_commands_escape_data_and_properties() -> None:
    v = ledger.Violation(
        "2026-01-01",
        "",
        "",
        "50% done\r\nnext; [x]",
        Decimal("0"),
        Decimal("0"),
        "x-test",
        "error",
        "a,b:c.bean",
        3,
    )
    assert diagnostics.github([v], "s") == (
        "::error file=a%2Cb%3Ac.bean,line=3,title=quints x-test::50%25 done%0D%0Anext; [x]\ns\n"
    )
    assert diagnostics.azure([v]) == (
        "##vso[task.logissue type=error;sourcepath=a,b:c.bean;linenumber=3;code=x-test]"
        "50%AZP25 done%0D%0Anext; [x]\n"
    )
    unplaced = ledger.Violation("", "", "", "gap", code="x-test", severity="warning")
    assert diagnostics.github([unplaced], "s") == "::warning title=quints x-test::gap\ns\n"
    assert diagnostics.text_line(unplaced) == "quints: warning: gap [x-test]"
