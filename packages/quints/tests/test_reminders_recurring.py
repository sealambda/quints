"""The recurring-invoice helper and payment reminders, as a user runs them."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quints.cli import app

runner = CliRunner()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    proj = tmp_path / "books"
    args = ["init", str(proj), "--name", "Muster GmbH", "--legal-form", "gmbh"]
    res = runner.invoke(app, [*args, "--lang", "en", "--vat-method", "effective", "--samples"])
    assert res.exit_code == 0, res.output
    monkeypatch.chdir(proj)
    return proj


def _json(*args: str) -> dict[str, object]:
    res = runner.invoke(app, [*args, "--json"])
    assert res.exit_code == 0, res.output
    return json.loads(res.stdout)


def test_next_invoice_then_remind_what_stays_open(project: Path) -> None:
    # The August invoice: July's file, the next free number, the new period.
    source = "invoicing/acme-2026-07.yaml"
    new = _json("invoice", "new", "--from", source, "--period", "2026-08", "--date", "2026-09-01")
    assert new == {
        "file": "invoicing/acme-2026-08.yaml",
        "number": "INV2026016",  # 015 is the sample export invoice
        "issue_date": "2026-09-01",
    }
    text = Path("invoicing/acme-2026-08.yaml").read_text()
    assert "supply: 2026-08\n" in text and "customer_reference: PO-2026-118" in text
    assert "# The customer's own reference" in text  # comments survive the copy

    # Never over an existing file; a new year restarts a series that carries it.
    res = runner.invoke(app, ["invoice", "new", "--from", source, "--period", "2026-08"])
    assert res.exit_code == 1 and "already exists" in res.stderr
    january = _json(
        "invoice", "new", "--from", source, "--period", "2026-12", "--date", "2027-01-05"
    )
    assert january["number"] == "INV2027001"
    Path(str(january["file"])).unlink()

    # Issue it, book it, and let the customer pay part of it.
    issued = _json("invoice", str(new["file"]))
    with Path("books/2026.bean").open("a") as f:
        f.write(
            f"\n{issued['ledger_draft']}\n\n"
            '2026-09-20 * "Acme AG" "Part payment" ^INV2026016\n'
            "  Assets:CH:GmbH:Current:UBS:CHF     581.00 CHF\n"
            "  Assets:CH:GmbH:Receivable:Trade   -581.00 CHF\n"
        )
    assert runner.invoke(app, ["check"]).exit_code == 0

    # 1 October: not yet due (net 30 from 1 September) — no reminder for it,
    # but the export invoice of 5 August is overdue.
    due = _json("receivables", "--remind", "--at", "2026-10-01")["reminders"]
    assert isinstance(due, list) and [r["number"] for r in due] == ["INV2026015"]

    reminders = _json("receivables", "--remind", "--at", "2026-10-15", "--days", "7")["reminders"]
    assert isinstance(reminders, list)
    by_number = {r["number"]: r for r in reminders}
    acme = by_number["INV2026016"]
    assert acme["open"] == "500.00" and acme["due"] == "2026-10-22"
    pdf = Path(acme["pdf"])
    assert pdf.name == "2026-10-15.acme.INV2026016.reminder.pdf" and pdf.exists()
    assert pdf.parent == Path(str(issued["pdf"])).parent  # filed beside the invoice
    # A reminder is not an invoice: no invoice data for software to book twice.
    assert b"factur-x.xml" not in pdf.read_bytes()
    # The invoice itself is untouched, and still verifies against its hash.
    assert _json("invoice", str(new["file"]))["pdf_sha256"] == issued["pdf_sha256"]
