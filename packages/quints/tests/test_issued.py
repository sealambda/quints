"""Issued means fixed: an invoice booked with its PDF's hash is verified
against it, never rendered again — the real steps, through the CLI."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from beancount import loader
from typer.testing import CliRunner

from quints import config
from quints.cli import app
from quints.invoice import verify

runner = CliRunner()

INVOICE = (
    "number: INV2026016\nkind: domestic\ncurrency: CHF\nissue_date: 2026-09-01\n"
    "supply: 2026-08\ncustomer: acme\n"
    "items:\n  - {description: Consulting, quantity: 1, unit_price: 1000.00}\n"
)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    proj = tmp_path / "books"
    args = ["init", str(proj), "--name", "Muster GmbH", "--legal-form", "gmbh"]
    res = runner.invoke(app, [*args, "--lang", "en", "--vat-method", "effective", "--samples"])
    assert res.exit_code == 0, res.output
    monkeypatch.chdir(proj)
    Path("invoicing/acme-2026-08.yaml").write_text(INVOICE)
    return proj


def _invoice(*extra: str) -> tuple[int, dict[str, object]]:
    res = runner.invoke(app, ["invoice", "invoicing/acme-2026-08.yaml", "--json", *extra])
    return res.exit_code, json.loads(res.stdout)


def test_issue_verify_tamper_and_reissue(project: Path) -> None:
    code, first = _invoice()
    assert code == 0 and first["issued"] is False
    pdf = Path(str(first["pdf"]))
    digest = verify.sha256(pdf)
    assert first["pdf_sha256"] == digest
    assert f'sha256: "{digest}"' in str(first["ledger_draft"])

    # Not booked yet: rendering again is harmless, and gives the same bytes.
    assert _invoice()[1]["pdf_sha256"] == digest

    # Booked with its hash, the invoice is issued: verified, not rendered.
    books = Path("books/2026.bean")
    books.write_text(books.read_text() + "\n" + str(first["ledger_draft"]) + "\n")
    stamp = pdf.stat().st_mtime_ns
    code, again = _invoice()
    assert code == 0 and again["issued"] is True and again["pdf_sha256"] == digest
    assert pdf.stat().st_mtime_ns == stamp
    assert runner.invoke(app, ["check"]).exit_code == 0

    # A changed customer no longer changes the PDF silently…
    registry = Path("invoicing/customers.yaml")
    registry.write_text(registry.read_text().replace("Acme AG", "Acme Holding AG"))
    assert _invoice()[1]["issued"] is True and verify.sha256(pdf) == digest
    # …a reissue has to be asked for, and has to say that it is one.
    code, refused = _invoice("--reissue")
    assert code == 1 and "corrected" in str(refused["errors"])
    Path("invoicing/acme-2026-08.yaml").write_text(INVOICE + "corrected: 2026-09-03\n")
    code, reissued = _invoice("--reissue")
    assert code == 0 and reissued["pdf_sha256"] != digest

    # The books still hold the old hash: both commands say so until it is booked.
    code, stale = _invoice()
    assert code == 1 and "not the PDF INV2026016 was issued as" in str(stale["errors"])
    res = runner.invoke(app, ["check", "--json"])
    assert res.exit_code == 1 and "SHA-256 differs" in res.stdout
    books.write_text(books.read_text().replace(digest, str(reissued["pdf_sha256"])))
    assert _invoice()[0] == 0 and runner.invoke(app, ["check"]).exit_code == 0

    pdf.unlink()
    code, missing = _invoice()
    assert code == 1 and "restore it from git" in str(missing["errors"])


def test_every_blocker_is_reported_as_data(project: Path) -> None:
    """One run lists the problems of every file and every check, with a code."""
    issuer = Path("invoicing/issuer.yaml")
    good = issuer.read_text()
    Path("invoicing/acme-2026-08.yaml").write_text(INVOICE.replace("CHF", "chf"))
    issuer.write_text(good + "email: [not, a, string]\n")
    code, d = _invoice()
    assert code == 1 and d["ok"] is False
    errors = d["errors"]
    assert isinstance(errors, list)
    assert {(e["file"], e["field"]) for e in errors} == {
        ("Invoice", "currency"),
        ("Issuer", "email"),
    }
    assert all(e["code"] and e["message"] for e in errors)

    # After loading: an export in a currency with no account, to a customer
    # with no VAT number — two checks, two entries.
    issuer.write_text(good)
    Path("invoicing/acme-2026-08.yaml").write_text(
        INVOICE.replace("domestic", "export").replace("CHF", "USD")
    )
    code, d = _invoice()
    assert code == 1
    assert [e["code"] for e in d["errors"]] == ["reverse_charge", "bank_account"]  # type: ignore[union-attr]
    res = runner.invoke(app, ["invoice", "invoicing/acme-2026-08.yaml"])
    assert res.stderr.startswith("ERROR: 2 problems:")


BOOKS = """
2026-01-01 open Assets:CH:GmbH:Receivable:Trade
2026-01-01 open Income:CH:GmbH:Consulting:External:Domestic
"""


def _booking(number: str, on: str) -> str:
    return (
        f'{on} * "ACME" "Work" ^{number}\n'
        "  Assets:CH:GmbH:Receivable:Trade  100.00 CHF\n"
        "  Income:CH:GmbH:Consulting:External:Domestic  -100.00 CHF\n"
    )


def test_audit_flags_duplicates_order_and_gaps(tmp_path: Path) -> None:
    text = BOOKS + "".join(
        _booking(n, d)
        for n, d in [
            ("INV2026001", "2026-02-01"),
            ("INV2026002", "2026-01-15"),  # dated before 001
            ("INV2026005", "2026-03-01"),  # 003 and 004 missing
            ("INV2026005", "2026-03-02"),  # booked twice
            ("INV2027001", "2027-01-10"),  # a new year is a new series: no gap
        ]
    )
    entries, _, _ = loader.load_string(text)
    errors, warnings = verify.audit(entries, tmp_path, config.Config())
    assert errors == [
        "invoice number INV2026005 is booked 2 times (2026-03-01, 2026-03-02)",
        "invoice INV2026002 (2026-01-15) is dated before INV2026001 (2026-02-01)",
    ]
    assert warnings == ["2 invoice number(s) missing between INV2026002 and INV2026005"]
    assert verify._series("INV-2026-014", date(2026, 7, 2).year) == ("INV-2026-", 14)  # pyright: ignore[reportPrivateUsage]
