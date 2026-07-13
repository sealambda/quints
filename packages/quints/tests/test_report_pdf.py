"""Tests for the statutory-statements PDF context (structure + i18n)."""

from pathlib import Path

from test_kmu_report import _LEDGER

from quints import kmu, report_pdf


def _reports(tmp_path):
    f = tmp_path / "main.bean"
    f.write_text(_LEDGER)
    return kmu.compute_bilanz(f, "2026-06-30"), kmu.compute_erfolg(f, "2026-01-01", "2026-12-31")


def test_bilanz_lines_structure_and_swiss_numbers(tmp_path):
    bilanz, _ = _reports(tmp_path)
    lines = report_pdf.bilanz_lines(bilanz, "de")
    assert lines[0]["label"] == "AKTIVEN" and lines[0]["bold"]
    totals = [ln for ln in lines if ln["rule"]]
    assert [ln["label"] for ln in totals] == ["Total Aktiven", "Total Passiven"]
    assert totals[0]["amount"] == "1'170.00"  # Swiss apostrophe format for de
    detail = next(ln for ln in lines if ln["code"] == "1020")
    assert detail["indent"] == 1 and detail["label"] == "Bankguthaben"

    english = report_pdf.bilanz_lines(bilanz, "en")
    assert next(ln["label"] for ln in english if ln["rule"]) == "Total assets"
    assert next(ln["amount"] for ln in english if ln["rule"]) == "1,170.00"


def test_erfolg_lines_flip_expense_signs(tmp_path):
    _, erfolg = _reports(tmp_path)
    lines = report_pdf.erfolg_lines(erfolg, "en")
    fee = next(ln for ln in lines if ln["code"] == "6940")
    assert fee["amount"] == "-10.00"  # expenses shown negative
    assert lines[-1]["label"] == "Profit/loss for the year"
    assert lines[-1]["amount"] == "180.00"


def test_render_pdf_produces_file(tmp_path):
    bilanz, erfolg = _reports(tmp_path)
    out = report_pdf.render_pdf(
        bilanz, erfolg, "de", tmp_path / "statements.pdf", issuer_path=Path("invoicing/issuer.yaml")
    )
    assert out.exists() and out.stat().st_size > 10_000
    assert out.read_bytes()[:5] == b"%PDF-"
