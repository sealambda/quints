"""eCH-0217 v2.0.0 — the VAT return as the XML the ESTV portal takes by upload.

"Spezifikation E-MWST" (eCH, genehmigt 2025-09-23) maps the return's Ziffern
onto one ``VATDeclaration``. The portal «MWST abrechnen» re-runs the standard's
Kap. 7 checks on upload and rejects the file on any of them:

- MWST-0001 schema — the element order below is the XSD's; it is not free.
- MWST-0005 Ziffer 299 = the turnover rows' total — :func:`quints.mwst.compute`
  guarantees it, or reports ``vat-turnover-total``.
- MWST-0006 ``payableTax`` = Σ rate × turnover, unrounded, minus the input tax
  — exactly how the report computes Ziffer 500/510.
- MWST-0002/3/4/7/8/9 method, period, single Ersteinreichung, rates and UID
  must match what the ESTV holds; only the portal can tell.

So a report with an error among its violations is refused: its figures are
known to be short.

Mapped: the effective method (``effectiveReportingMethod``, turnover net) and
the Saldosteuersatz — ``simpleTaxRateMethod`` per Tätigkeit from 2025,
``netTaxRateMethod`` per rate before. Not mapped: the Pauschalsteuersatz, the
Jahresabstimmung (typeOfSubmission 3: differences only), the
Schlussabrechnung, collected-revenue accounting, and the pre-2025 SSS
deductions (Ziffern 470/471, Formulare 1050/1055/1056).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterable
from datetime import UTC, datetime
from datetime import date as Date
from decimal import Decimal

from . import __version__, ledger
from .invoice import vatid
from .mwst import MwstReport, RateRow

_NS = {
    "eCH-0217": "http://www.ech.ch/xmlns/eCH-0217/2",
    "eCH-0058": "http://www.ech.ch/xmlns/eCH-0058/5",
}
# From this period on the SSS return goes by Tätigkeit (simpleTaxRateMethod).
_SIMPLE_FROM = "2025-01-01"


def _el(parent: ET.Element, name: str, text: str | None = None, ns: str = "eCH-0217") -> ET.Element:
    el = ET.SubElement(parent, f"{{{_NS[ns]}}}{name}")
    el.text = text
    return el


def _amounts(parent: ET.Element, *fields: tuple[str, Decimal]) -> None:
    """Optional amounts — a zero is left out, never sent empty (Kap. 4.1)."""
    for name, value in fields:
        if value:
            _el(parent, name, f"{value:.2f}")


def _rows(parent: ET.Element, name: str, rows: Iterable[tuple[str, Decimal, Decimal]]) -> None:
    """(Tätigkeit, rate, turnover) lines, cumulated per rate and Tätigkeit (Kap. 3.2)."""
    totals: dict[tuple[str, Decimal], Decimal] = {}
    for activity, rate, turnover in rows:
        totals[activity, rate] = totals.get((activity, rate), Decimal("0")) + turnover
    for (activity, rate), turnover in totals.items():
        if not turnover:
            continue
        el = _el(parent, name)
        if activity:
            _el(el, "activityID", activity)
        _el(el, "taxRate", f"{(rate * 100).normalize():f}")  # 0.081 → "8.1"
        _el(el, "turnover", f"{turnover:.2f}")


def _activity(row: RateRow) -> str:
    """The activityID of a Saldosteuersatz row (Kap. 5.3.11)."""
    if not row.current:
        # A supply made before 2024, declared now: the standard's technical
        # ids T0011-T0020, one per 2023 rate in ascending order (Tab. 15).
        # shortcut: 2024 supplies declared from 2025 on (T0001-T0010) are not
        # told apart from current ones; add a vintage for them if it comes up.
        rates = ledger.saldo_rates(Date(2023, 12, 31))
        if row.rate not in rates:
            raise ValueError(
                f"a supply from before 2024 is declared at {row.rate * 100:g} %, which was "
                "not a Saldosteuersatz then — enter this return in the portal by hand"
            )
        return f"T{rates.index(row.rate) + 11:04d}"
    if not row.activity:
        raise ValueError(
            f"[[vat.saldo]] rate {row.label} has no `activity` in quints.toml — add the "
            "5-character Tätigkeitscode the ESTV portal shows under Abrechnungsmodalitäten"
        )
    return row.activity


def to_xml(
    report: MwstReport,
    *,
    name: str,
    uid: str,
    reference: str,
    generated: datetime,
    correction: bool = False,
) -> bytes:
    """The return as a ``VATDeclaration`` document.

    ``name`` and ``uid`` are the entity's as the UID register has them;
    ``reference`` is the sender's own id for this return (≤ 50 characters);
    ``correction`` makes it a Korrekturabrechnung, which replaces the return
    already filed for the period. Raises :class:`ValueError` for a return that
    must not be filed as it stands.
    """
    errors = [v for v in report.violations if v.severity == "error"]
    if errors:
        raise ValueError(
            f"{len(errors)} problem(s) in the books for {report.date_from}..{report.date_to} "
            "— the return would be wrong. `quints vat report` lists them; fix them first."
        )
    digits = vatid.swiss_uid_digits(uid)
    if digits is None:
        raise ValueError(f"{uid!r} is not a Swiss UID (CHE-123.456.789) — the return needs one")

    for prefix, uri in _NS.items():
        ET.register_namespace(prefix, uri)
    root = ET.Element(f"{{{_NS['eCH-0217']}}}VATDeclaration")

    info = _el(root, "generalInformation")
    _el(info, "uid", f"CHE{digits}")
    _el(info, "organisationName", " ".join(name.split())[:255])
    _el(info, "generationTime", generated.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
    # Shorter than the calendar period where liability starts or ends (Kap. 7.3).
    _el(info, "reportingPeriodFrom", report.liable_from or report.date_from)
    _el(info, "reportingPeriodTill", report.liable_to or report.date_to)
    _el(info, "typeOfSubmission", "2" if correction else "1")
    _el(info, "formOfReporting", "1")  # vereinbart: quints computes on agreed Entgelte
    _el(info, "businessReferenceId", reference[:50])
    sender = _el(info, "sendingApplication")
    _el(sender, "manufacturer", "Sealambda", ns="eCH-0058")
    _el(sender, "product", "quints", ns="eCH-0058")
    _el(sender, "productVersion", __version__[:10], ns="eCH-0058")

    turnover = _el(root, "turnoverComputation")
    _el(turnover, "totalConsideration", f"{report.z200:.2f}")
    _amounts(
        turnover,
        ("suppliesToForeignCountries", report.z220),
        ("suppliesAbroad", report.z221),
        ("transferNotificationProcedure", report.z225),
        ("suppliesExemptFromTax", report.z230),
        ("reductionOfConsideration", report.z235),
    )
    if report.z280:
        various = _el(turnover, "variousDeduction")
        _el(various, "amountVariousDeduction", f"{report.z280:.2f}")
        _el(various, "descriptionVariousDeduction", "Diverses")

    saldo = report.vat_method == "saldo"
    if not saldo:
        method = _el(root, "effectiveReportingMethod")
        _el(method, "grossOrNet", "1")  # net, as the ESTV recommends
        _amounts(method, ("opted", report.z205))
    elif report.date_to >= _SIMPLE_FROM:
        method = _el(root, "simpleTaxRateMethod")
    else:
        method = _el(root, "netTaxRateMethod")
    by_activity = method.tag.endswith("simpleTaxRateMethod")
    _rows(
        method,
        "suppliesPerTaxRate",
        ((_activity(r) if by_activity else "", r.rate, r.net) for r in report.rate_rows if r.net),
    )
    _rows(method, "acquisitionTax", (("", r.rate, r.net) for r in report.bezug_rows))
    if not saldo:
        _amounts(
            method,
            ("inputTaxMaterialAndServices", report.z400),
            ("inputTaxInvestments", report.z405),
            ("subsequentInputTaxDeduction", report.z410),
            ("inputTaxCorrections", report.z415),
            ("inputTaxReductions", report.z420),
        )

    _el(root, "payableTax", f"{report.z500:.2f}")  # signed: a credit (510) is negative
    if report.z900 or report.z910:
        flows = _el(root, "otherFlowsOfFunds")
        _amounts(flows, ("subsidies", report.z900), ("donations", report.z910))

    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
