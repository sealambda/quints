"""A user error must read as one `ERROR:` line, never as a traceback.

The domain modules raise `ValueError` (pydantic's `ValidationError` included)
for everything someone can get wrong in their own files. `cli._CleanErrors`
turns those into the same `ERROR: …` line every explicit check in the CLI
prints, so "your YAML says X" never arrives as a Python stack trace.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from quints.cli import app

runner = CliRunner()

ISSUER = """
name: Muster GmbH
address: [Musterstrasse 1, 3000 Bern]
vat_id: CHE-267.359.056 MWST
bank:
  EUR:
    iban: {iban}
{bic}
"""

INVOICE = """
number: INV1
kind: export
currency: EUR
issue_date: 2026-08-05
customer:
  name: Globex Ltd
  address: [1 Liffey Street, Dublin 1]
  country: IE
  vat_id: IE1234567T
items:
  - {description: Consulting, quantity: 1, unit_price: 500.00}
"""


def _project(
    tmp_path: Path,
    iban: str = "DE89 3704 0044 0532 0130 00",
    bic: str | None = None,
    invoice: str = INVOICE,
) -> list[str]:
    (tmp_path / "issuer.yaml").write_text(
        ISSUER.format(iban=iban, bic=f"    bic: {bic}" if bic else "")
    )
    (tmp_path / "inv.yaml").write_text(invoice)
    return [
        "invoice",
        str(tmp_path / "inv.yaml"),
        "--issuer",
        str(tmp_path / "issuer.yaml"),
        "--out",
        str(tmp_path / "out.pdf"),
        "--no-verify",
    ]


def test_domain_error_is_one_error_line(tmp_path: Path) -> None:
    res = runner.invoke(app, _project(tmp_path))  # no bic on an export invoice
    assert res.exit_code == 1
    out = res.stderr
    assert out.startswith("ERROR: export invoice INV1 in EUR has no `bic`")
    assert "Traceback" not in out and "ValueError" not in out


def test_authoring_error_names_the_field_without_pydantic_noise(tmp_path: Path) -> None:
    bad = "DE89 3704 0044 0532 0130 01"  # last digit off — mod-97 fails
    res = runner.invoke(app, _project(tmp_path, iban=bad))
    assert res.exit_code == 1
    out = res.stderr
    assert out.startswith("ERROR: Issuer: bank.EUR.iban — ")
    assert "is not a valid IBAN" in out
    # Pydantic's own rendering, which this replaces.
    assert "Value error," not in out
    assert "[type=value_error" not in out and "errors.pydantic.dev" not in out
    assert "Traceback" not in out


def test_every_bad_field_is_listed(tmp_path: Path) -> None:
    args = _project(tmp_path, iban="DE89 3704 0044 0532 0130 01", bic="COBADEFFXX")
    res = runner.invoke(app, args)
    out = res.stderr
    assert out.startswith("ERROR: 2 problems in Issuer:")
    assert "bank.EUR.iban — " in out and "bank.EUR.bic — " in out


def test_union_field_reports_no_pydantic_schema_tags(tmp_path: Path) -> None:
    """`customer` is `str | Party`, so pydantic tags each complaint with the
    union arm it came from — schema names, not keys anyone can edit."""
    res = runner.invoke(
        app, _project(tmp_path, invoice=INVOICE.replace("IE1234567T", "IE9999999X"))
    )
    assert res.exit_code == 1
    out = res.stderr
    assert "customer — invalid VAT number 'IE9999999X' for country IE" in out
    assert "function-after" not in out and "customer.str" not in out


def test_whole_model_validator_needs_no_field_name(tmp_path: Path) -> None:
    """A checksum validator runs on the model, not a field — its loc is empty,
    so the sentence stands alone instead of trailing a bare `(root) —`."""
    (tmp_path / "customers.yaml").write_text(
        "acme:\n  name: Acme Ltd\n  address: [1 Liffey Street, Dublin 1]\n"
        "  country: IE\n  vat_id: IE9999999X\n"
    )
    args = _project(
        tmp_path,
        invoice=INVOICE.replace(
            "customer:\n  name: Globex Ltd\n  address: [1 Liffey Street, Dublin 1]\n"
            "  country: IE\n  vat_id: IE1234567T\n",
            "customer: acme\n",
        ),
    )
    res = runner.invoke(app, [*args, "--customers", str(tmp_path / "customers.yaml")])
    assert res.exit_code == 1
    assert res.stderr.startswith("ERROR: Party: invalid VAT number 'IE9999999X' for country IE")
    assert "(root)" not in res.stderr and " — " not in res.stderr


def test_qr_bill_branch_reports_cleanly_too(tmp_path: Path) -> None:
    """The domestic path raises from inside the qrbill library, not from a
    model — it must arrive as the same one-liner."""
    domestic = INVOICE.replace("kind: export", "kind: domestic").replace(
        "currency: EUR", "currency: USD"
    )
    args = _project(tmp_path, invoice=domestic)
    issuer = tmp_path / "issuer.yaml"
    issuer.write_text(issuer.read_text().replace("  EUR:", "  USD:"))
    res = runner.invoke(app, args)
    assert res.exit_code == 1
    assert res.stderr.startswith("ERROR: Swiss QR-bill supports only CHF or EUR, not 'USD'")
    assert "Traceback" not in res.stderr


def test_traceback_escape_hatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A quints bug still needs a stack trace to debug."""
    monkeypatch.setenv("QUINTS_TRACEBACK", "1")
    res = runner.invoke(app, _project(tmp_path))
    assert isinstance(res.exception, ValueError)
