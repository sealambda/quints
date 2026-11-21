"""`quints diff`, `quints digest` and `quints report bundle`: what a CI job
needs beyond the gate, in-process and runner-agnostic.

- diff: what a change does to the books, in money, for a PR comment;
- digest: the state of the books on one page, never failing;
- bundle: every statutory file of a year in one call.
"""

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quints.cli import app

runner = CliRunner()

CHART = """\
option "documents" "documents"
2026-01-01 open Assets:CH:GmbH:Current:UBS:CHF CHF
  kmu: "1020"
2026-01-01 open Assets:CH:GmbH:Receivable:Trade CHF
  kmu: "1100"
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

2026-01-02 * "Owner" "capital"
  Assets:CH:GmbH:Current:UBS:CHF   20000.00 CHF
  Equity:CH:GmbH:Capital          -20000.00 CHF
"""
SALE = """
2026-02-02 * "Acme AG" "Consulting" ^INV2026001
  document: "2026-02-02.acme.INV2026001.pdf"
  Assets:CH:GmbH:Receivable:Trade                1081.00 CHF
  Income:CH:GmbH:Consulting:External:Domestic   -1000.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT               -81.00 CHF
"""
PURCHASE = """
2026-02-10 * "Supplier" "material"
  Expenses:CH:GmbH:Material          500.00 CHF
  Assets:CH:GmbH:Current:UBS:CHF    -500.00 CHF
"""
SETTLE_Q1 = """
2026-03-31 * "ESTV" "2026-Q1 VAT settlement" ^VAT-2026-Q1
  due: 2026-05-30
  Liabilities:CH:GmbH:Tax:OutputVAT     81.00 CHF
  Liabilities:CH:GmbH:Tax:PayableVAT   -81.00 CHF
"""
TOML = "[entity]\nvat_registered_since = 2026-01-01\n"


def _books(root: Path, body: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "quints.toml").write_text(TOML)
    led = root / "main.bean"
    led.write_text(CHART + body)
    return led


def test_diff_lists_added_bookings_and_moving_figures(tmp_path: Path) -> None:
    base = _books(tmp_path / "base", SALE)
    head = _books(tmp_path / "head", SALE + PURCHASE)
    argv = ["diff", "--base", str(base), "--at", "2026-03-31", "-f", str(head)]
    res = runner.invoke(app, [*argv, "--output-format", "json"])
    assert res.exit_code == 0, res.output
    d = json.loads(res.stdout)
    assert d["base_loaded"] and d["transactions"] == 3 and d["removed"] == []
    (added,) = d["added"]
    assert (added["narration"], added["amounts"], added["documented"]) == (
        "material",
        "500.00 CHF",
        False,
    )
    assert added["line"] == 27  # located in the head
    moved = {f["label"]: f["change"] for f in d["figures"]}
    assert moved == {
        "Result 2026": "-500.00",
        "EBIT 2026": "-500.00",
        "Total assets 2026-03-31": "-500.00",
    }
    md = runner.invoke(app, [*argv, "--output-format", "markdown"]).stdout
    assert md.startswith("## Ledger impact\n") and "**1 added**" in md
    assert "| Result 2026 | 1,000.00 | 500.00 | -500.00 CHF |" in md
    assert "· 500.00 CHF · no document" in md


def test_diff_counts_a_missing_base_as_empty(tmp_path: Path) -> None:
    head = _books(tmp_path, SALE)
    res = runner.invoke(
        app, ["diff", "--base", str(tmp_path / "gone.bean"), "--at", "2026-03-31", "-f", str(head)]
    )
    assert res.exit_code == 0
    assert "did not load: compared against empty books" in res.stdout
    assert "2 added" in res.stdout


def _git(repo: Path, *args: str) -> None:
    identity = ["-c", "user.name=Test", "-c", "user.email=test@example.com"]
    subprocess.run(["git", "-C", str(repo), *identity, *args], check=True, capture_output=True)  # noqa: S603, S607 — test fixture


def test_diff_reads_the_base_from_a_git_ref(tmp_path: Path) -> None:
    led = _books(tmp_path / "books", SALE)  # in a subdirectory of the repository
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    led.write_text(CHART + SALE + PURCHASE)
    argv = ["diff", "--base-ref", "HEAD", "--at", "2026-03-31", "-f", str(led)]
    res = runner.invoke(app, argv)
    assert res.exit_code == 0, res.output
    assert "1 added, 0 removed" in res.stdout and "Result 2026: 1,000.00 → 500.00" in res.stdout
    assert not list(tmp_path.glob("**/quints-base-*"))  # nothing left in the repository
    res = runner.invoke(app, ["diff", "--base-ref", "nope", "-f", str(led)])
    assert res.exit_code == 1 and "fetch-depth: 0" in res.stderr


def test_digest_reports_and_never_fails(tmp_path: Path) -> None:
    led = _books(tmp_path, SALE + PURCHASE + SETTLE_Q1)
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "2026-04-01.acme.receipt.pdf").write_bytes(b"%PDF receipt")
    (tmp_path / "documents").mkdir()
    (tmp_path / "documents" / "2026-02-02.acme.INV2026001.pdf").write_bytes(b"%PDF")
    (tmp_path / "documents" / "stray.pdf").write_bytes(b"%PDF")
    res = runner.invoke(
        app, ["digest", "--today", "2026-07-20", "--output-format", "json", "-f", str(led)]
    )
    assert res.exit_code == 0, res.output
    d = json.loads(res.stdout)
    assert [(u["period"], u["due"]) for u in d["unsettled"]] == [("VAT-2026-Q2", "2026-08-29")]
    assert d["running"]["period"] == "VAT-2026-Q3"
    assert [(x["period"], x["owed"]) for x in d["unpaid"]] == [("VAT-2026-Q1", "81.00")]
    assert [o["number"] for o in d["receivables"]] == ["INV2026001"]
    assert d["inbox"] == ["2026-04-01.acme.receipt.pdf"]
    assert [u["narration"] for u in d["undocumented"]] == ["material"]
    assert d["documented_of"] == 2 and d["orphans"] == ["documents/stray.pdf"]
    assert any("INV2026001" in a and "168 days" in a for a in d["alerts"])
    md = runner.invoke(
        app, ["digest", "--today", "2026-07-20", "--output-format", "markdown", "-f", str(led)]
    ).stdout
    assert "## VAT" in md and "| VAT-2026-Q2 | 2026-08-29 | 40 |" in md


@pytest.mark.parametrize("calendar", [True, False])
def test_bundle_writes_every_statutory_file(tmp_path: Path, calendar: bool) -> None:
    led = _books(tmp_path / "books", SALE + SETTLE_Q1)
    out = tmp_path / "out"
    period = ["--year", "2026"] if calendar else ["--from", "2026-01-01", "--to", "2026-06-30"]
    res = runner.invoke(
        app,
        ["report", "bundle", *period, "--lang", "en", "--out", str(out), "--json", "-f", str(led)],
    )
    assert res.exit_code == 0, res.output
    files = json.loads(res.stdout)["files"]
    stem = "2026" if calendar else "2026-01-01_2026-06-30"
    quarters = ["Q1", "Q2", "Q3", "Q4"] if calendar else ["Q1", "Q2"]
    assert files == [
        f"statements-{stem}.en.pdf",
        f"bilanz-{stem}.en.txt",
        f"erfolg-{stem}.en.txt",
        f"konten-{stem}.en.txt",
        f"bilanz-{stem}.json",
        f"erfolg-{stem}.json",
        *(f"mwst-2026-{q}.json" for q in quarters),
    ]
    assert all((out / f).stat().st_size for f in files)
    text = (out / f"erfolg-{stem}.en.txt").read_text()
    assert "\x1b[" not in text and "1,000.00" in text  # plain text, no terminal codes
    assert json.loads((out / "mwst-2026-Q1.json").read_text())["z200"] == "1000.00"
