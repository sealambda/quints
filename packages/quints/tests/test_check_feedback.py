"""What `quints check` gives an agent between two edits, through the CLI.

A CI run tells the whole truth once. An agent, above all a small local model,
needs less and sooner: a code that names the mistake, only the problems its
own edit brought, the fix when there is one, and the explanation offline.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quints import config, init
from quints.cli import app

runner = CliRunner()
EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
BANK = "Assets:CH:GmbH:Current:UBS:CHF"

CHART = f"""\
plugin "quints.plugins.kmu" ":CH:GmbH:"
2026-01-01 open {BANK} CHF
  kmu: "1020"
2026-01-01 open Assets:CH:GmbH:Receivable:Trade CHF
  kmu: "1100"
2026-01-01 open Income:CH:GmbH:Consulting:External:Export CHF
  kmu: "3400"
2026-01-01 open Expenses:CH:GmbH:Material CHF
  kmu: "4400"
"""


def _check(led: Path, *args: str) -> tuple[int, dict[str, list[dict[str, object]]]]:
    res = runner.invoke(app, ["check", "--json", "-f", str(led), *args])
    return res.exit_code, json.loads(res.stdout)


def _codes(report: dict[str, list[dict[str, object]]]) -> list[str]:
    return sorted(str(d["code"]) for d in report["errors"] + report["warnings"])


def test_a_loader_error_names_what_went_wrong(tmp_path: Path) -> None:
    led = tmp_path / "main.bean"
    purchase = (
        f'2026-02-01 * "Shop" "Paper"\n  Expenses:CH:GmbH:Material  10 CHF\n  {BANK}  -10 CHF\n'
    )
    led.write_text(
        'plugin "beancount.plugins.noduplicates"\n'
        + CHART
        + 'include "nope.bean"\n'
        + "2026-01-01 open Assets:CH:GmbH:Cash CHF\n"  # no kmu: code
        + purchase
        + purchase  # the same statement imported twice
        + f"2026-03-01 balance {BANK}  5 CHF\n"
        + "2026-03-02 garbage\n"
    )
    code, report = _check(led)
    assert code == 1
    assert set(_codes(report)) == {
        "kmu-missing",
        "ledger-balance",
        "ledger-duplicate",
        "ledger-load",
        "ledger-syntax",
    }
    (duplicate,) = [d for d in report["errors"] if d["code"] == "ledger-duplicate"]
    # beancount prints both entries whole, with this machine's paths.
    assert duplicate["message"] == "ledger error: the same entry is booked twice"


def test_review_lists_what_is_still_to_do_only_when_asked(tmp_path: Path) -> None:
    led = tmp_path / "main.bean"
    led.write_text(
        CHART
        + f'2026-02-01 ! "Shop" "Draft"\n  Expenses:CH:GmbH:Material  10 CHF\n  {BANK}  -10 CHF\n'
        + f'2026-06-01 * "Shop" "Paper"\n  Expenses:CH:GmbH:Material  10 CHF\n  {BANK}  -10 CHF\n'
    )
    code, report = _check(led)
    assert code == 0 and _codes(report) == []  # the gate has nothing to say
    code, report = _check(led, "--review")
    assert code == 0 and not report["errors"]
    # The draft is reported as a draft, not also for its missing document.
    assert _codes(report) == ["balance-stale", "document-missing", "flag-pending"]
    assert _check(led, "--review", "--strict")[0] == 1

    # An assertion after the last posting, the flag cleared, a document linked.
    led.write_text(
        led.read_text().replace(" ! ", " * ").replace('"Paper"\n', '"Paper"\n  document: "r.pdf"\n')
        + f"2026-06-02 balance {BANK}  -20 CHF\n"
    )
    code, report = _check(led, "--review")
    assert _codes(report) == ["document-missing"]  # the former draft still has none


def _git(repo: Path, *args: str) -> None:
    identity = ["-c", "user.name=Test", "-c", "user.email=test@example.com"]
    subprocess.run(["git", "-C", str(repo), *identity, *args], check=True, capture_output=True)  # noqa: S603, S607 — test fixture


@pytest.fixture
def scaffold(tmp_path: Path) -> Path:
    """The example project as `quints init` leaves it: a repository, committed."""
    proj = tmp_path / "books"
    shutil.copytree(EXAMPLES, proj)
    _git(proj, "init", "-q")
    _git(proj, "add", ".")
    _git(proj, "commit", "-q", "-m", "scaffold")
    return proj


def test_the_scaffolded_hook_reports_only_what_an_edit_brought(scaffold: Path) -> None:
    settings = json.loads((scaffold / ".claude/settings.json").read_text())
    (entry,) = settings["hooks"]["PostToolUse"]
    command = entry["hooks"][0]["command"]
    assert entry["matcher"] == "Edit|Write"
    # Exit 2 is what hands a hook's stderr back to the model.
    assert "uv run quints check" in command and command.endswith("1>&2 || exit 2")
    argv = command.split("uv run quints ")[1].split(" 1>&2")[0].split()
    assert argv == ["check", "--review", "--strict", "--base-ref", "HEAD"]
    led = ["-f", str(scaffold / "main.bean")]

    # The sample quarter has entries to review, but the last commit has them too.
    assert _check(scaffold / "main.bean", "--review")[1]["warnings"]
    res = runner.invoke(app, [*argv, *led])
    assert res.exit_code == 0, res.output
    assert "no new problems since HEAD" in res.stdout

    books = scaffold / "books" / "2026.bean"
    books.write_text(
        books.read_text()
        + '\n2026-08-21 ! "Shop" "Paper"\n'
        + "  Expenses:CH:GmbH:Admin:Bookkeeping  10.00 CHF\n"
        + f"  {BANK}  -11.00 CHF\n"
    )
    res = runner.invoke(app, [*argv, *led])
    assert res.exit_code == 1
    problems = [line for line in res.stderr.splitlines() if line.startswith("books/2026.bean:")]
    assert [line.rsplit("[", 1)[1] for line in problems] == ["ledger]", "flag-pending]"]
    assert "1 error(s), 1 warning(s)." in res.stderr


def test_fix_books_the_hash_of_a_filed_invoice(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    filed = tmp_path / "documents" / "Income" / "INV2026001.pdf"
    filed.parent.mkdir(parents=True)
    filed.write_bytes(b"%PDF-1.7 the issued invoice")
    led = tmp_path / "main.bean"
    booking = (
        '2026-03-01 * "Acme AG" "Work" ^INV2026001\n'
        '  invoice: "INV2026001"\n'
        '  document: "INV2026001.pdf"\n'
        "  Assets:CH:GmbH:Receivable:Trade  100.00 CHF\n"
        "  Income:CH:GmbH:Consulting:External:Export  -100.00 CHF\n"
    )
    led.write_text(CHART + booking)
    _, report = _check(led)
    (warning,) = report["warnings"]
    assert warning["code"] == "invoice-unhashed"
    fix = warning["fix"]
    assert isinstance(fix, dict)
    assert (fix["file"], fix["after_line"]) == ("main.bean", warning["line"])
    assert str(warning["help"]).endswith("/reference/diagnostics/#invoice-unhashed")

    code, report = _check(led, "--fix")
    assert code == 0 and report["fixed"] == 1 and not report["warnings"]
    header, inserted = led.read_text().splitlines()[9:11]
    assert header.startswith("2026-03-01 *") and inserted == fix["insert"]
    assert _check(led, "--fix")[1]["fixed"] == 0  # nothing left to fix


def test_explain_reads_the_reference_offline() -> None:
    res = runner.invoke(app, ["explain", "vat-mismatch"])
    assert res.exit_code == 0 and "Error. The output VAT a sale posts" in res.stdout
    listed = runner.invoke(app, ["explain"]).stdout.splitlines()
    assert "ledger-syntax: error" in listed and "flag-pending: warning" in listed
    res = runner.invoke(app, ["explain", "vat_mismatch"])
    assert res.exit_code == 1 and "did you mean vat-mismatch?" in res.stderr


def test_quints_toml_has_a_schema_an_editor_can_check_against(tmp_path: Path) -> None:
    schema = config.json_schema()
    entity = schema["properties"]["entity"]  # type: ignore[index]
    assert entity["additionalProperties"] is False
    assert entity["properties"]["legal_form"]["enum"] == ["gmbh", "ag", "einzelfirma"]
    change = schema["properties"]["vat"]["properties"]["change"]["items"]  # type: ignore[index]
    assert change["properties"]["from"] == {"type": "string", "format": "date"}
    # The scaffold points taplo at the hosted copy, on the first line.
    files = {str(f.path): f.content for f in init.plan(init.Answers())}
    assert files["quints.toml"].startswith(
        f"#:schema {config.DOCS_URL}/schema/quints.schema.json\n"
    )
    res = runner.invoke(app, ["schema", "--out", str(tmp_path)])
    assert res.exit_code == 0
    assert json.loads((tmp_path / "quints.schema.json").read_text()) == schema
