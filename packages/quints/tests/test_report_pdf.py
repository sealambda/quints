"""Tests for the statutory-statements PDF context (structure + i18n)."""

from pathlib import Path

from quints import kmu, report_pdf

LEDGER = """
2024-01-01 open Assets:CH:GmbH:Current:UBS:CHF CHF
    kmu: "1020"
2024-01-01 open Assets:CH:GmbH:Current:Wise:EUR EUR
    kmu: "1020"
2024-01-01 open Assets:CH:GmbH:Receivable:Trade
    kmu: "1100"
2024-01-01 open Liabilities:CH:GmbH:Loan:Ivan CHF
    kmu: "2500"
2024-01-01 open Equity:CH:GmbH:Contributions:Ivan CHF
    kmu: "2800"
2024-01-01 open Income:CH:GmbH:Consulting:External:Export
    kmu: "3400"
2024-01-01 open Expenses:CH:GmbH:IT:Hosting
    kmu: "6570"
2024-01-01 open Expenses:CH:GmbH:BankFees:UBS CHF
    kmu: "6940"

2025-06-01 * "Old supplier" "expense before the report year"
    Expenses:CH:GmbH:IT:Hosting                50.00 CHF
    Liabilities:CH:GmbH:Loan:Ivan

2026-01-10 * "Owner" "capital"
    Assets:CH:GmbH:Current:UBS:CHF           1000.00 CHF
    Equity:CH:GmbH:Contributions:Ivan

2026-02-01 price EUR 0.95 CHF

2026-02-05 * "Client" "export invoiced"
    Assets:CH:GmbH:Receivable:Trade           200.00 EUR
    Income:CH:GmbH:Consulting:External:Export

2026-02-10 * "Client" "export received"
    Assets:CH:GmbH:Current:Wise:EUR           200.00 EUR
    Assets:CH:GmbH:Receivable:Trade          -200.00 EUR

2026-03-01 * "Bank" "fee"
    Expenses:CH:GmbH:BankFees:UBS              10.00 CHF
    Assets:CH:GmbH:Current:UBS:CHF

2026-06-01 price EUR 0.90 CHF
"""


def _reports(tmp_path: Path) -> tuple[kmu.BilanzReport, kmu.ErfolgReport]:
    f = tmp_path / "main.bean"
    f.write_text(LEDGER)
    return kmu.compute_bilanz(f, "2026-06-30"), kmu.compute_erfolg(f, "2026-01-01", "2026-12-31")


def test_bilanz_lines_structure_and_swiss_numbers(tmp_path: Path):
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


def test_erfolg_lines_flip_expense_signs(tmp_path: Path):
    _, erfolg = _reports(tmp_path)
    lines = report_pdf.erfolg_lines(erfolg, "en")
    fee = next(ln for ln in lines if ln["code"] == "6940")
    assert fee["amount"] == "-10.00"  # expenses shown negative
    assert lines[-1]["label"] == "Profit/loss for the year"
    assert lines[-1]["amount"] == "180.00"


def test_render_pdf_produces_file(tmp_path: Path):
    bilanz, erfolg = _reports(tmp_path)
    out = report_pdf.render_pdf(
        bilanz, erfolg, "de", tmp_path / "statements.pdf", issuer_path=Path("invoicing/issuer.yaml")
    )
    assert out.exists() and out.stat().st_size > 10_000
    assert out.read_bytes()[:5] == b"%PDF-"


def test_prior_year_column_keeps_lines_only_one_year_has(tmp_path: Path):
    f = tmp_path / "main.bean"
    f.write_text(LEDGER)
    erfolg = kmu.compute_erfolg(f, "2026-01-01", "2026-12-31")
    prior = kmu.compute_erfolg(f, "2025-01-01", "2025-12-31")
    lines = report_pdf.with_prior(
        report_pdf.erfolg_lines(erfolg, "en"), report_pdf.erfolg_lines(prior, "en")
    )
    by_code = {ln["code"]: ln for ln in lines if ln["code"]}
    assert (by_code["6570"]["amount"], by_code["6570"]["prior"]) == ("", "-50.00")
    assert (by_code["6940"]["amount"], by_code["6940"]["prior"]) == ("-10.00", "")
    assert (lines[-1]["amount"], lines[-1]["prior"]) == ("180.00", "-50.00")
    # the 2025-only row keeps its statutory place: after revenue, before EBIT
    order = [ln["code"] or ln["label"] for ln in lines]
    assert order.index("3400") < order.index("6570") < order.index("Operating result (EBIT)")
    # every line of either year, each once
    assert len(lines) == len({(ln["code"], ln["label"]) for ln in lines})


def test_render_pdf_with_prior_year(tmp_path: Path):
    bilanz, erfolg = _reports(tmp_path)
    f = tmp_path / "main.bean"
    prior = (
        kmu.compute_bilanz(f, "2025-12-31", date_from="2025-01-01"),
        kmu.compute_erfolg(f, "2025-01-01", "2025-12-31"),
    )
    ctx = report_pdf.build_context(bilanz, erfolg, "de", prior=prior)
    assert ctx["columns"] == ["2026", "2025"]
    out = report_pdf.render_pdf(bilanz, erfolg, "de", tmp_path / "s.pdf", prior=prior)
    assert out.read_bytes()[:5] == b"%PDF-"
