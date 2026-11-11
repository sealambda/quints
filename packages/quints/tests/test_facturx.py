"""Every invoice PDF carries itself as Factur-X data — checked against the
official EN 16931 schema and business rules (schematron), not our reading of
them. The oracles ship with the `factur-x` package; Saxon runs the rules."""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path

import facturx
import pytest
from lxml import etree  # pyright: ignore[reportAttributeAccessIssue]
from saxonche import PySaxonProcessor
from typer.testing import CliRunner

from quints.cli import app

runner = CliRunner()
_RULES = Path(facturx.__file__).parent / "xsd_and_schematron/facturx-en16931/FACTUR-X_EN16931.xslt"
_FAILED = re.compile(r"<svrl:failed-assert[^>]*>.*?<svrl:text>(.*?)</svrl:text>", re.S)

HEAD = "kind: domestic\ncurrency: CHF\nissue_date: 2026-09-01\nsupply: 2026-08\ncustomer: acme\n"
EVERYTHING = (
    "number: INV2026016\n" + HEAD + "customer_reference: PO-77\n"
    "items:\n"
    "  - {description: Workshop, quantity: 1, unit_price: 1250.00, discount: 20}\n"
    "  - {description: Handbook, quantity: 10, unit_price: 33.33, vat_rate: 2.6}\n"
    "  - {description: Coaching, quantity: 1.5, unit_price: 180.00, unit: Std.}\n"
    "discount: {percent: 5}\n"
    "pass_through:\n  - {description: Commercial register fee, amount: 120.00}\n"
)
EXPORT = (
    "number: INV2026017\nkind: export\ncurrency: EUR\nissue_date: 2026-09-01\n"
    "supply: 2026-08\ncustomer: globex\n"
    "items:\n  - {description: Consulting, quantity: 2.5, unit_price: 200.00}\n"
    "discount: {amount: 50}\n"
)
CASES = {
    "several rates, discounts, pass-through": EVERYTHING,
    "credit note": EVERYTHING.replace("INV2026016", "CN2026001") + "credit_note_for: INV2026016\n",
    "export, reverse charge": EXPORT,
    "export, no reverse charge": EXPORT + "reverse_charge: false\n",
    "single rate": "number: INV2026018\n" + HEAD + "items:\n"
    "  - {description: Work, quantity: 3, unit_price: 99.95}\n",
}


@cache
def _saxon() -> PySaxonProcessor:
    return PySaxonProcessor(license=False)


def _broken_rules(xml: bytes) -> list[str]:
    proc = _saxon()
    rules = proc.new_xslt30_processor().compile_stylesheet(stylesheet_file=str(_RULES))
    report = rules.transform_to_string(xdm_node=proc.parse_xml(xml_text=xml.decode()))
    return [" ".join(m.split()) for m in _FAILED.findall(report or "")]


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    proj = tmp_path_factory.mktemp("facturx") / "books"
    args = ["init", str(proj), "--name", "Muster GmbH", "--legal-form", "gmbh"]
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
        res = runner.invoke(app, [*args, "--lang", "en", "--vat-method", "effective", "--samples"])
    assert res.exit_code == 0, res.output
    return proj


@pytest.mark.parametrize("case", CASES)
def test_embedded_invoice_passes_the_official_validators(
    case: str, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(project)
    Path("invoicing/case.yaml").write_text(CASES[case])
    res = runner.invoke(app, ["invoice", "invoicing/case.yaml", "-o", "case.pdf", "--no-verify"])
    assert res.exit_code == 0, res.output

    # Read back the way a recipient does; this validates against the XSD.
    name, xml = facturx.get_xml_from_pdf(Path("case.pdf").read_bytes(), check_xsd=True)
    assert name == "factur-x.xml" and isinstance(xml, bytes)
    assert facturx.get_level(etree.fromstring(xml)) == "en16931"
    assert not _broken_rules(xml), "\n".join(_broken_rules(xml))
    # Declared in the XMP, which is what makes the PDF a Factur-X document.
    assert b"<fx:ConformanceLevel>EN 16931</fx:ConformanceLevel>" in Path("case.pdf").read_bytes()


def test_the_xml_carries_the_amounts_the_pdf_prints(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(project)
    Path("invoicing/case.yaml").write_text(EVERYTHING)
    res = runner.invoke(app, ["invoice", "invoicing/case.yaml", "-o", "case.pdf", "--json"])
    totals = json.loads(res.stdout)["totals"]
    _, xml = facturx.get_xml_from_pdf(Path("case.pdf").read_bytes())
    assert isinstance(xml, bytes)
    text = xml.decode()
    for tag, key in [
        ("LineTotalAmount", "subtotal"),
        ("AllowanceTotalAmount", "discount"),
        ("ChargeTotalAmount", "pass_through"),
        ("RoundingAmount", "rounding"),
        ("DuePayableAmount", "amount_due"),
    ]:
        assert f">{totals[key]}</ram:{tag}>" in text, tag
    assert f'currencyID="CHF">{totals["vat_amount"]}</ram:TaxTotalAmount>' in text


def test_a_pro_forma_carries_no_invoice_data(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(project)
    Path("invoicing/case.yaml").write_text(
        CASES["single rate"].replace("INV2026018", "PF2026001") + "proforma: true\n"
    )
    res = runner.invoke(app, ["invoice", "invoicing/case.yaml", "-o", "pf.pdf", "--no-verify"])
    assert res.exit_code == 0, res.output
    assert b"factur-x.xml" not in Path("pf.pdf").read_bytes()


def test_units_travel_as_standard_codes(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(project)
    Path("invoicing/case.yaml").write_text(EVERYTHING)
    runner.invoke(app, ["invoice", "invoicing/case.yaml", "-o", "case.pdf", "--no-verify"])
    _, xml = facturx.get_xml_from_pdf(Path("case.pdf").read_bytes())
    assert isinstance(xml, bytes)
    # `Std.` is hours; a line without a unit is a plain unit.
    assert xml.count(b'unitCode="HUR"') == 1 and xml.count(b'unitCode="C62"') == 2


def test_an_unregistered_issuer_is_outside_the_scope_of_vat(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before registration: no VAT, no VAT identifier — the bare UID travels
    as the seller's legal registration, and the validators still pass."""
    monkeypatch.chdir(project)
    issuer = Path("invoicing/issuer.yaml").read_text().replace(" MWST", "")
    Path("invoicing/issuer-before.yaml").write_text(issuer)
    Path("invoicing/case.yaml").write_text(
        CASES["single rate"].replace("2026-09-01", "2025-12-15").replace("2026-08", "2025-12")
    )
    args = ["invoice", "invoicing/case.yaml", "--issuer", "invoicing/issuer-before.yaml"]
    res = runner.invoke(app, [*args, "-o", "case.pdf", "--no-verify"])
    assert res.exit_code == 0, res.output
    _, xml = facturx.get_xml_from_pdf(Path("case.pdf").read_bytes(), check_xsd=True)
    assert isinstance(xml, bytes)
    assert not _broken_rules(xml), "\n".join(_broken_rules(xml))
    assert b"<ram:CategoryCode>O</ram:CategoryCode>" in xml and b'schemeID="VA"' not in xml
    assert b"<ram:TaxTotalAmount" in xml and b">0.00</ram:TaxTotalAmount>" in xml
