"""What a line can carry beyond quantity × price: its own VAT rate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quints.cli import app

runner = CliRunner()

MIXED = (
    "number: INV2026016\nkind: domestic\ncurrency: CHF\nissue_date: 2026-09-01\n"
    "supply: 2026-08\ncustomer: acme\n"
    "items:\n"
    "  - {description: Workshop, quantity: 1, unit_price: 1000.00}\n"
    "  - {description: Handbook, quantity: 10, unit_price: 33.33, vat_rate: 2.6}\n"
)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    proj = tmp_path / "books"
    args = ["init", str(proj), "--name", "Muster GmbH", "--legal-form", "gmbh"]
    res = runner.invoke(app, [*args, "--lang", "en", "--vat-method", "effective", "--samples"])
    assert res.exit_code == 0, res.output
    monkeypatch.chdir(proj)
    return proj


def _issue(text: str) -> dict[str, object]:
    """Render the invoice and book its draft, as the guide has it."""
    Path("invoicing/new.yaml").write_text(text)
    res = runner.invoke(app, ["invoice", "invoicing/new.yaml", "--json"])
    assert res.exit_code == 0, res.output
    d = json.loads(res.stdout)
    with Path("books/2026.bean").open("a") as f:
        f.write("\n" + d["ledger_draft"] + "\n")
    return d


def _vat_report() -> dict[str, object]:
    res = runner.invoke(app, ["vat", "report", "-p", "2026-Q3", "--json"])
    assert res.exit_code == 0, res.output
    return json.loads(res.stdout)


def test_two_vat_rates_on_one_invoice(project: Path) -> None:
    before = _vat_report()
    d = _issue(MIXED)
    totals = d["totals"]
    assert isinstance(totals, dict)
    # VAT per rate group, each rounded once: 81.00 + 8.67 (2.6 % of 333.30).
    assert [(g["rate"], g["net"], g["vat"]) for g in totals["vat_groups"]] == [
        ("8.1", "1000.00", "81.00"),
        ("2.6", "333.30", "8.67"),
    ]
    assert totals["vat_amount"] == "89.67" and totals["grand_total"] == "1422.95"
    draft = str(d["ledger_draft"])
    assert 'mwst: "reduced"' in draft and draft.count(":Domestic ") == 2

    # Booked as drafted, the books check out and the return files each rate.
    assert runner.invoke(app, ["check"]).exit_code == 0
    after = _vat_report()
    assert after["violations"] == before["violations"]
    assert json.dumps(after) != json.dumps(before)
    res = runner.invoke(app, ["invoice", "invoicing/new.yaml", "--json"])
    assert json.loads(res.stdout)["cross_check"]["ok"]


def test_a_line_rate_must_be_in_force(project: Path) -> None:
    Path("invoicing/new.yaml").write_text(MIXED.replace("2.6", "2.5"))
    res = runner.invoke(app, ["invoice", "invoicing/new.yaml"])
    assert res.exit_code == 1 and "items.1.vat_rate 2.5 is not a Swiss VAT rate" in res.stderr
