"""What a line can carry beyond quantity × price: its own VAT rate."""

from __future__ import annotations

import json
from decimal import Decimal
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


def test_discounts_reduce_the_consideration_per_rate(project: Path) -> None:
    """A line discount comes off its line; the invoice's is spread over the
    rates in proportion, and VAT is charged on what is left."""
    d = _issue(
        MIXED.replace("unit_price: 1000.00}", "unit_price: 1250.00, discount: 20}")
        + "discount: {amount: 100}\n"
    )
    totals = d["totals"]
    assert isinstance(totals, dict)
    assert totals["subtotal"] == "1333.30" and totals["discount"] == "100.00"
    # 100 × 333.30 / 1333.30 = 25.00 off the reduced group, the rest off the other.
    assert [(g["net"], g["vat"]) for g in totals["vat_groups"]] == [
        ("925.00", "74.93"),
        ("308.30", "8.02"),
    ]
    assert totals["grand_total"] == "1316.25"
    assert runner.invoke(app, ["check"]).exit_code == 0
    res = runner.invoke(app, ["invoice", "invoicing/new.yaml", "--json"])
    assert json.loads(res.stdout)["cross_check"]["ok"]
    assert not _vat_report()["violations"]


def test_a_discount_is_percent_or_amount_and_no_more_than_the_lines(project: Path) -> None:
    for discount, message in [
        ("{percent: 5, amount: 10}", "either `percent` or `amount`"),
        ("{amount: 5000}", "exceeds its lines"),
    ]:
        Path("invoicing/new.yaml").write_text(MIXED + f"discount: {discount}\n")
        res = runner.invoke(app, ["invoice", "invoicing/new.yaml"])
        assert res.exit_code == 1 and message in res.stderr, res.stderr


def test_pass_through_items_are_outside_vat_and_revenue(project: Path) -> None:
    """An outlay in the customer's name: a receivable when paid, cleared by
    the invoice that passes it on — never income, never VAT."""
    with Path("books/2026.bean").open("a") as f:
        f.write(
            '\n2026-08-20 * "Handelsregisteramt" "Fee paid for Acme AG"\n'
            "  Assets:CH:GmbH:Receivable:Disbursements  120.00 CHF\n"
            "  Assets:CH:GmbH:Current:UBS:CHF          -120.00 CHF\n"
        )
    before = _vat_report()
    d = _issue(
        "number: INV2026016\nkind: domestic\ncurrency: CHF\nissue_date: 2026-09-01\n"
        "supply: 2026-08\ncustomer: acme\n"
        "items:\n  - {description: Filing, quantity: 1, unit_price: 500.00}\n"
        "pass_through:\n  - {description: Commercial register fee, amount: 120.00}\n"
    )
    totals = d["totals"]
    assert isinstance(totals, dict)
    assert totals["vat_amount"] == "40.50" and totals["pass_through"] == "120.00"
    assert totals["grand_total"] == "660.50"
    assert "Receivable:Disbursements" in str(d["ledger_draft"])
    assert runner.invoke(app, ["check"]).exit_code == 0
    res = runner.invoke(app, ["invoice", "invoicing/new.yaml", "--json"])
    assert json.loads(res.stdout)["cross_check"]["ok"]
    # The return gains the 500.00 and its VAT, not the outlay.
    after = _vat_report()
    assert not after["violations"]
    assert Decimal(str(after["z303_tax"])) - Decimal(str(before["z303_tax"])) == Decimal("40.50")
