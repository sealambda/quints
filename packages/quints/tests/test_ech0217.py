"""The VAT return as eCH-0217 XML — checked against the official schema and
recomputed the way the standard says the ESTV portal does on upload (Kap. 6
and 7), not against our reading of our own figures.

The schemas in ``fixtures/ech0217`` are the published ones (eCH-0217 2.0.0,
eCH-0058 5.0; eCH-0108 7.0 cut down to the two types a return uses).
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from pathlib import Path

import pytest
from lxml import etree  # pyright: ignore[reportAttributeAccessIssue]
from typer.testing import CliRunner

from quints import config, ech0217, mwst
from quints.cli import app

runner = CliRunner()
XSD = Path(__file__).parent / "fixtures" / "ech0217"
EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
NS = {"v": "http://www.ech.ch/xmlns/eCH-0217/2", "a": "http://www.ech.ch/xmlns/eCH-0058/5"}
UID = "CHE-267.359.056 MWST"
AT = datetime(2026, 10, 5, 8, 30, tzinfo=UTC)

_CHART = """
2024-01-01 open Assets:CH:GmbH:Current:UBS:CHF CHF
  kmu: "1020"
2024-01-01 open Assets:CH:GmbH:Tax:InputVAT CHF
  kmu: "1170"
2024-01-01 open Assets:CH:GmbH:Equipment:Office CHF
  kmu: "1510"
2024-01-01 open Liabilities:CH:GmbH:Tax:OutputVAT CHF
  kmu: "2200"
2024-01-01 open Liabilities:CH:GmbH:Tax:Bezugsteuer CHF
  kmu: "2200"
2024-01-01 open Income:CH:GmbH:Consulting:External:Domestic CHF
  kmu: "3400"
2024-01-01 open Income:CH:GmbH:Consulting:External:Export CHF
  kmu: "3400"
2024-01-01 open Income:CH:GmbH:Books:Reduced CHF
  kmu: "3200"
2024-01-01 open Income:CH:GmbH:Trade:Handel CHF
  kmu: "3200"
2024-01-01 open Income:CH:GmbH:Grants CHF
  kmu: "3600"
2024-01-01 open Expenses:CH:GmbH:IT:Hosting CHF
  kmu: "6570"
2024-01-01 open Expenses:CH:GmbH:Tax:Bezugsteuer CHF
  kmu: "6570"
"""

# Seven small sales: 1.05 x 8.1 % is 0.08505, so each invoice charges 0.09 —
# 0.63 in the books against the 0.60 the form computes on 7.35.
_SMALL_SALES = "".join(
    f"""
2026-07-{day:02d} * "Walk-in" "Small sale"
  Assets:CH:GmbH:Current:UBS:CHF                    1.14 CHF
  Income:CH:GmbH:Consulting:External:Domestic      -1.05 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT                -0.09 CHF
"""
    for day in range(1, 8)
)

_EFFECTIVE = (
    _CHART
    + _SMALL_SALES
    + """
2026-07-10 * "Buchhandlung" "Books"
  Assets:CH:GmbH:Current:UBS:CHF                  513.00 CHF
  Income:CH:GmbH:Books:Reduced                   -500.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT               -13.00 CHF

2026-07-12 * "Globex Ltd" "Export consulting"
  Assets:CH:GmbH:Current:UBS:CHF                  300.00 CHF
  Income:CH:GmbH:Consulting:External:Export      -300.00 CHF

2026-07-15 * "Kanton" "Förderbeitrag"
  Assets:CH:GmbH:Current:UBS:CHF                  250.00 CHF
  Income:CH:GmbH:Grants                          -250.00 CHF
    mwst: "subvention"

2026-08-12 * "Foreign SaaS" "Cloud hosting (reverse charge)"
  Expenses:CH:GmbH:IT:Hosting                      92.96 CHF
  Assets:CH:GmbH:Tax:InputVAT                       7.53 CHF
  Liabilities:CH:GmbH:Tax:Bezugsteuer              -7.53 CHF
  Assets:CH:GmbH:Current:UBS:CHF                  -92.96 CHF

2026-08-20 * "Möbel AG" "Desk"
  Assets:CH:GmbH:Equipment:Office                1000.00 CHF
  Assets:CH:GmbH:Tax:InputVAT                      81.00 CHF
  Assets:CH:GmbH:Current:UBS:CHF                -1081.00 CHF

2026-09-30 * "Eigenverbrauch" "Vorsteuerkorrektur (Art. 31)"
  Assets:CH:GmbH:Tax:InputVAT                     -30.00 CHF
    mwst: "vorsteuerkorrektur"
  Expenses:CH:GmbH:IT:Hosting                      30.00 CHF
"""
)

_SALDO = replace(
    config.Config(),
    vat_method="saldo",
    saldo=(
        config.SaldoRate(Decimal("0.062"), activity="10042"),
        config.SaldoRate(Decimal("0.013"), marker=":Handel", activity="10007"),
    ),
)
_SALDO_BOOKS = (
    _CHART
    + """
{year}-07-02 * "Acme AG" "Consulting"
  Assets:CH:GmbH:Current:UBS:CHF                 1081.00 CHF
  Income:CH:GmbH:Consulting:External:Domestic   -1000.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT               -81.00 CHF

{year}-07-05 * "Laden" "Handel"
  Assets:CH:GmbH:Current:UBS:CHF                  540.50 CHF
  Income:CH:GmbH:Trade:Handel                    -500.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT               -40.50 CHF

{year}-08-12 * "Foreign SaaS" "Cloud hosting (reverse charge)"
  Expenses:CH:GmbH:IT:Hosting                      92.96 CHF
  Expenses:CH:GmbH:Tax:Bezugsteuer                  7.53 CHF
  Liabilities:CH:GmbH:Tax:Bezugsteuer              -7.53 CHF
  Assets:CH:GmbH:Current:UBS:CHF                  -92.96 CHF
"""
)


@cache
def _schema() -> etree.XMLSchema:
    """The published schema, its ``xs:import`` of www.ech.ch served from the fixtures."""
    text = (XSD / "eCH-0217-2-0-0.xsd").read_text()
    local = re.sub(r'schemaLocation="[^"]*/', f'schemaLocation="{XSD.as_uri()}/', text)
    return etree.XMLSchema(etree.fromstring(local.encode()))


def _valid(xml: bytes) -> etree._Element:
    """Parse, and hold the document to the published schema (MWST-0001)."""
    root = etree.fromstring(xml)
    _schema().assertValid(root)
    return root


def _amount(node: etree._Element, path: str) -> Decimal:
    return Decimal(node.findtext(path, default="0", namespaces=NS))


def _recomputed(root: etree._Element) -> tuple[Decimal, Decimal]:
    """(Ziffer 299 − the turnover rows, payableTax) by Kap. 6.2-6.4, from the file alone."""
    turnover = root.find("v:turnoverComputation", NS)
    (method,) = [c for c in root if c.tag.endswith("Method")]
    z299 = _amount(turnover, "v:totalConsideration") - sum(
        _amount(turnover, f"v:{name}")
        for name in (
            "suppliesToForeignCountries",
            "suppliesAbroad",
            "transferNotificationProcedure",
            "suppliesExemptFromTax",
            "reductionOfConsideration",
            "variousDeduction/v:amountVariousDeduction",
        )
    )
    supplies = method.findall("v:suppliesPerTaxRate", NS)
    tax = sum(
        _amount(r, "v:taxRate") / 100 * _amount(r, "v:turnover")
        for r in (*supplies, *method.findall("v:acquisitionTax", NS))
    )
    tax -= sum(
        _amount(method, f"v:{name}")
        for name in (
            "inputTaxMaterialAndServices",
            "inputTaxInvestments",
            "subsequentInputTaxDeduction",
        )
    )
    tax += _amount(method, "v:inputTaxCorrections") + _amount(method, "v:inputTaxReductions")
    left = z299 - sum(_amount(r, "v:turnover") for r in supplies)
    return left, tax.quantize(Decimal("0.01"), ROUND_HALF_UP)


def _export(tmp_path: Path, books: str, period: str, cfg: config.Config | None = None) -> bytes:
    led = tmp_path / "m.bean"
    led.write_text(books)
    report = mwst.compute(led, *mwst.period_range(period), cfg or config.Config())
    return ech0217.to_xml(
        report, name="Muster GmbH", uid=UID, reference=f"VAT-{period}", generated=AT
    )


def test_effective_return_is_valid_and_recomputes(tmp_path: Path) -> None:
    xml = _export(tmp_path, _EFFECTIVE, "2026-Q3")
    assert xml.startswith(b"<?xml version='1.0' encoding='utf-8'?>\n")
    root = _valid(xml)

    info = root.find("v:generalInformation", NS)
    assert info.findtext("v:uid", namespaces=NS) == "CHE267359056"
    assert info.findtext("v:generationTime", namespaces=NS) == "2026-10-05T08:30:00Z"
    assert info.findtext("v:reportingPeriodFrom", namespaces=NS) == "2026-07-01"
    assert info.findtext("v:reportingPeriodTill", namespaces=NS) == "2026-09-30"
    assert info.findtext("v:typeOfSubmission", namespaces=NS) == "1"
    assert info.findtext("v:businessReferenceId", namespaces=NS) == "VAT-2026-Q3"
    assert info.findtext("v:sendingApplication/a:product", namespaces=NS) == "quints"

    method = root.find("v:effectiveReportingMethod", NS)
    assert method.findtext("v:grossOrNet", namespaces=NS) == "1"
    rows = {
        r.findtext("v:taxRate", namespaces=NS): r.findtext("v:turnover", namespaces=NS)
        for r in method.findall("v:suppliesPerTaxRate", NS)
    }
    assert rows == {"8.1": "7.35", "2.6": "500.00"}  # zero rows (3.8 %) are left out
    assert method.findtext("v:acquisitionTax/v:turnover", namespaces=NS) == "92.96"
    assert method.findtext("v:inputTaxInvestments", namespaces=NS) == "88.53"
    assert method.findtext("v:inputTaxCorrections", namespaces=NS) == "30.00"
    assert method.find("v:inputTaxMaterialAndServices", NS) is None  # zero: left out
    assert root.findtext("v:otherFlowsOfFunds/v:subsidies", namespaces=NS) == "250.00"

    # MWST-0005 and MWST-0006, as the portal checks them on upload. The tax
    # is 7.35 x 8.1 % + 13.00 + 7.53, not the 0.63 + 13.00 + 7.53 posted.
    left, payable = _recomputed(root)
    assert left == 0
    assert payable == _amount(root, "v:payableTax") == Decimal("-37.40")


def test_saldo_return_goes_by_activity_from_2025(tmp_path: Path) -> None:
    root = _valid(_export(tmp_path, _SALDO_BOOKS.format(year=2026), "2026-H2", _SALDO))
    method = root.find("v:simpleTaxRateMethod", NS)
    rows = {
        r.findtext("v:activityID", namespaces=NS): (
            r.findtext("v:taxRate", namespaces=NS),
            r.findtext("v:turnover", namespaces=NS),
        )
        for r in method.findall("v:suppliesPerTaxRate", NS)
    }
    assert rows == {"10042": ("6.2", "1081.00"), "10007": ("1.3", "540.50")}  # gross
    # Bezugsteuer stays at the statutory rate, net.
    assert method.findtext("v:acquisitionTax/v:taxRate", namespaces=NS) == "8.1"
    left, payable = _recomputed(root)
    assert left == 0
    assert payable == _amount(root, "v:payableTax") == Decimal("81.58")


def test_saldo_return_before_2025_goes_by_rate(tmp_path: Path) -> None:
    root = _valid(_export(tmp_path, _SALDO_BOOKS.format(year=2024), "2024-H2", _SALDO))
    method = root.find("v:netTaxRateMethod", NS)
    assert method.find("v:suppliesPerTaxRate/v:activityID", NS) is None
    left, payable = _recomputed(root)
    assert left == 0 and payable == _amount(root, "v:payableTax")


def test_saldo_rate_without_an_activity_is_refused(tmp_path: Path) -> None:
    cfg = replace(_SALDO, saldo=(config.SaldoRate(Decimal("0.062")),))
    with pytest.raises(ValueError, match=r"rate 6\.2 has no `activity`"):
        _export(tmp_path, _SALDO_BOOKS.format(year=2026), "2026-H2", cfg)
    with pytest.raises(config.ConfigError, match="5 characters"):
        config.validate(replace(cfg, saldo=(config.SaldoRate(Decimal("0.062"), activity="42"),)))


def test_a_return_with_errors_or_without_a_uid_is_refused(tmp_path: Path) -> None:
    wrong_rate = """
2026-09-01 * "Acme AG" "VAT at last year's rate"
  Assets:CH:GmbH:Current:UBS:CHF                  107.70 CHF
  Income:CH:GmbH:Consulting:External:Domestic    -100.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT                -7.70 CHF
"""
    with pytest.raises(ValueError, match=r"1 problem\(s\) in the books"):
        _export(tmp_path, _EFFECTIVE + wrong_rate, "2026-Q3")
    report = mwst.compute(tmp_path / "m.bean", "2026-07-01", "2026-08-31", config.Config())
    with pytest.raises(ValueError, match="not a Swiss UID"):
        ech0217.to_xml(report, name="Muster GmbH", uid="DE123456789", reference="x", generated=AT)


def test_cli_exports_the_example_project(tmp_path: Path) -> None:
    out = tmp_path / "vat.xml"
    argv = ["vat", "export", "-p", "2026-Q3", "-f", str(EXAMPLES / "main.bean")]
    argv += ["--issuer", str(EXAMPLES / "invoicing" / "issuer.yaml")]
    res = runner.invoke(app, [*argv, "--correction", "--output-file", str(out)])
    assert res.exit_code == 0, res.output
    root = _valid(out.read_bytes())
    assert root.findtext("v:generalInformation/v:typeOfSubmission", namespaces=NS) == "2"
    left, payable = _recomputed(root)
    assert left == 0 and payable == _amount(root, "v:payableTax")

    stdout = runner.invoke(app, argv)
    assert stdout.exit_code == 0 and b"<eCH-0217:VATDeclaration" in stdout.stdout_bytes

    missing = runner.invoke(app, [*argv[:-1], str(tmp_path / "nope.yaml")])
    assert missing.exit_code == 1 and "issuer config not found" in missing.output
