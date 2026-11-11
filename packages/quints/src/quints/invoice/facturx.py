"""Factur-X / ZUGFeRD: the invoice as UN/CEFACT CII XML, EN 16931 profile.

The XML rides inside the PDF (PDF/A-3) as `factur-x.xml`, so one file is both
the invoice a person reads and the one a machine books. What German and
Austrian customers ask for today, and what the EU makes mandatory for
intra-EU B2B from 1 July 2030 (ViDA, Directive (EU) 2025/516).

`to_xml` writes; `embed_metadata` adds the XMP declaration that makes a
PDF with that attachment a Factur-X document.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal

from .structured import AllowanceCharge, StructuredInvoice, Tax, TradeParty

FILENAME = "factur-x.xml"
PROFILE = "urn:cen.eu:en16931:2017"
_NS = {
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
    "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
}


def _el(parent: ET.Element, tag: str, text: str | None = None, **attrs: str) -> ET.Element:
    prefix, name = tag.split(":")
    el = ET.SubElement(parent, f"{{{_NS[prefix]}}}{name}", attrs)
    el.text = text
    return el


def _amount(v: Decimal) -> str:
    return f"{v:.2f}"


def _number(v: Decimal) -> str:
    """A quantity, price or rate: plain decimal, no exponent, no trailing zeros."""
    return f"{v.normalize():f}"


def _date(parent: ET.Element, tag: str, d: date) -> None:
    _el(_el(parent, tag), "udt:DateTimeString", d.strftime("%Y%m%d"), format="102")


def _tax(parent: ET.Element, tag: str, tax: Tax) -> None:
    el = _el(parent, tag)
    _el(el, "ram:TypeCode", "VAT")
    _el(el, "ram:CategoryCode", tax.category)
    if tax.rate is not None:
        _el(el, "ram:RateApplicablePercent", _number(tax.rate))


def _party(parent: ET.Element, tag: str, p: TradeParty) -> None:
    el = _el(parent, tag)
    _el(el, "ram:Name", p.name)
    if p.legal_id:
        _el(_el(el, "ram:SpecifiedLegalOrganization"), "ram:ID", p.legal_id)
    address = _el(el, "ram:PostalTradeAddress")
    # As printed: free lines, never guessed apart into street, postcode and town.
    lines = [*p.address[:2], ", ".join(p.address[2:])]
    for name, line in zip(("LineOne", "LineTwo", "LineThree"), lines, strict=False):
        if line:
            _el(address, f"ram:{name}", line)
    _el(address, "ram:CountryID", p.country)
    if p.vat_id:
        _el(_el(el, "ram:SpecifiedTaxRegistration"), "ram:ID", p.vat_id, schemeID="VA")


def _allowance_charge(parent: ET.Element, ac: AllowanceCharge) -> None:
    el = _el(parent, "ram:SpecifiedTradeAllowanceCharge")
    _el(_el(el, "ram:ChargeIndicator"), "udt:Indicator", "true" if ac.charge else "false")
    _el(el, "ram:ActualAmount", _amount(ac.amount))
    _el(el, "ram:Reason", ac.reason)
    _tax(el, "ram:CategoryTradeTax", ac.tax)


def _lines(deal: ET.Element, doc: StructuredInvoice) -> None:
    for ln in doc.lines:
        item = _el(deal, "ram:IncludedSupplyChainTradeLineItem")
        _el(_el(item, "ram:AssociatedDocumentLineDocument"), "ram:LineID", ln.id)
        _el(_el(item, "ram:SpecifiedTradeProduct"), "ram:Name", ln.name)
        price = _el(_el(item, "ram:SpecifiedLineTradeAgreement"), "ram:NetPriceProductTradePrice")
        _el(price, "ram:ChargeAmount", _number(ln.unit_price))
        # shortcut: every quantity is in units (C62); map `unit` to UN/ECE
        # Recommendation 20 codes when a recipient needs hours or days told apart.
        _el(
            _el(item, "ram:SpecifiedLineTradeDelivery"),
            "ram:BilledQuantity",
            _number(ln.quantity),
            unitCode="C62",
        )
        settlement = _el(item, "ram:SpecifiedLineTradeSettlement")
        _tax(settlement, "ram:ApplicableTradeTax", ln.tax)
        if ln.allowance:
            allowance = _el(settlement, "ram:SpecifiedTradeAllowanceCharge")
            _el(_el(allowance, "ram:ChargeIndicator"), "udt:Indicator", "false")
            _el(allowance, "ram:ActualAmount", _amount(ln.allowance))
            _el(allowance, "ram:ReasonCode", "95")  # UNTDID 5189: discount
        _el(
            _el(settlement, "ram:SpecifiedTradeSettlementLineMonetarySummation"),
            "ram:LineTotalAmount",
            _amount(ln.net),
        )


def _settlement(deal: ET.Element, doc: StructuredInvoice) -> None:
    s = _el(deal, "ram:ApplicableHeaderTradeSettlement")
    _el(s, "ram:PaymentReference", doc.payment_reference)
    _el(s, "ram:InvoiceCurrencyCode", doc.currency)
    means = _el(s, "ram:SpecifiedTradeSettlementPaymentMeans")
    # UNTDID 4461: 58 is a SEPA credit transfer, 30 any other.
    _el(means, "ram:TypeCode", "58" if doc.currency == "EUR" else "30")
    _el(_el(means, "ram:PayeePartyCreditorFinancialAccount"), "ram:IBANID", doc.iban)
    if doc.bic:
        _el(_el(means, "ram:PayeeSpecifiedCreditorFinancialInstitution"), "ram:BICID", doc.bic)
    for b in doc.breakdown:
        tax = _el(s, "ram:ApplicableTradeTax")
        _el(tax, "ram:CalculatedAmount", _amount(b.amount))
        _el(tax, "ram:TypeCode", "VAT")
        if b.tax.reason:
            _el(tax, "ram:ExemptionReason", b.tax.reason)
        _el(tax, "ram:BasisAmount", _amount(b.basis))
        _el(tax, "ram:CategoryCode", b.tax.category)
        if b.tax.rate is not None:
            _el(tax, "ram:RateApplicablePercent", _number(b.tax.rate))
    period = _el(s, "ram:BillingSpecifiedPeriod")
    _date(period, "ram:StartDateTime", doc.period[0])
    _date(period, "ram:EndDateTime", doc.period[1])
    for ac in doc.allowances_charges:
        _allowance_charge(s, ac)
    if doc.due_date:
        _date(_el(s, "ram:SpecifiedTradePaymentTerms"), "ram:DueDateDateTime", doc.due_date)
    total = _el(s, "ram:SpecifiedTradeSettlementHeaderMonetarySummation")
    _el(total, "ram:LineTotalAmount", _amount(doc.line_total))
    _el(total, "ram:ChargeTotalAmount", _amount(doc.charge_total))
    _el(total, "ram:AllowanceTotalAmount", _amount(doc.allowance_total))
    _el(total, "ram:TaxBasisTotalAmount", _amount(doc.tax_basis_total))
    _el(total, "ram:TaxTotalAmount", _amount(doc.tax_total), currencyID=doc.currency)
    _el(total, "ram:RoundingAmount", _amount(doc.rounding))
    _el(total, "ram:GrandTotalAmount", _amount(doc.grand_total))
    _el(total, "ram:TotalPrepaidAmount", _amount(doc.prepaid))
    _el(total, "ram:DuePayableAmount", _amount(doc.due))
    if doc.preceding_invoice:
        _el(
            _el(s, "ram:InvoiceReferencedDocument"),
            "ram:IssuerAssignedID",
            doc.preceding_invoice,
        )


def to_xml(doc: StructuredInvoice) -> bytes:
    """The CII document. Element order is the schema's; it is not free."""
    for prefix, uri in _NS.items():
        ET.register_namespace(prefix, uri)
    root = ET.Element(f"{{{_NS['rsm']}}}CrossIndustryInvoice")
    context = _el(root, "rsm:ExchangedDocumentContext")
    _el(_el(context, "ram:GuidelineSpecifiedDocumentContextParameter"), "ram:ID", PROFILE)
    header = _el(root, "rsm:ExchangedDocument")
    _el(header, "ram:ID", doc.number)
    _el(header, "ram:TypeCode", "381" if doc.credit_note else "380")  # UNTDID 1001
    _date(header, "ram:IssueDateTime", doc.issue_date)
    for note in doc.notes:
        _el(_el(header, "ram:IncludedNote"), "ram:Content", note)

    deal = _el(root, "rsm:SupplyChainTradeTransaction")
    _lines(deal, doc)
    agreement = _el(deal, "ram:ApplicableHeaderTradeAgreement")
    _party(agreement, "ram:SellerTradeParty", doc.seller)
    _party(agreement, "ram:BuyerTradeParty", doc.buyer)
    if doc.order_reference:
        _el(
            _el(agreement, "ram:BuyerOrderReferencedDocument"),
            "ram:IssuerAssignedID",
            doc.order_reference,
        )
    # BT-72, the day the supply was completed: the end of its period.
    delivery = _el(
        _el(deal, "ram:ApplicableHeaderTradeDelivery"), "ram:ActualDeliverySupplyChainEvent"
    )
    _date(delivery, "ram:OccurrenceDateTime", doc.period[1])
    _settlement(deal, doc)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


# ── the PDF side ──────────────────────────────────────────────────────────────

_FX = "urn:factur-x:pdfa:CrossIndustryDocument:invoice:1p0#"


def _property(name: str, description: str) -> str:
    return (
        '<rdf:li rdf:parseType="Resource">'
        f"<pdfaProperty:name>{name}</pdfaProperty:name>"
        "<pdfaProperty:valueType>Text</pdfaProperty:valueType>"
        "<pdfaProperty:category>external</pdfaProperty:category>"
        f"<pdfaProperty:description>{description}</pdfaProperty:description>"
        "</rdf:li>"
    )


_SCHEMA = (
    '<rdf:li rdf:parseType="Resource">'
    "<pdfaSchema:schema>Factur-X PDFA Extension Schema</pdfaSchema:schema>"
    f"<pdfaSchema:namespaceURI>{_FX}</pdfaSchema:namespaceURI>"
    "<pdfaSchema:prefix>fx</pdfaSchema:prefix>"
    "<pdfaSchema:property><rdf:Seq>"
    + _property("DocumentFileName", "The name of the embedded XML document")
    + _property("DocumentType", "The type of the hybrid document in capital letters")
    + _property("Version", "The version of the standard applying to the embedded XML document")
    + _property("ConformanceLevel", "The conformance level of the embedded XML document")
    + "</rdf:Seq></pdfaSchema:property></rdf:li>"
)
_DESCRIPTION = (
    f'<rdf:Description rdf:about="" xmlns:fx="{_FX}">'
    "<fx:DocumentType>INVOICE</fx:DocumentType>"
    f"<fx:DocumentFileName>{FILENAME}</fx:DocumentFileName>"
    "<fx:Version>1.0</fx:Version>"
    "<fx:ConformanceLevel>EN 16931</fx:ConformanceLevel>"
    "</rdf:Description>"
)
_METADATA = re.compile(
    rb"(\d+) 0 obj\n<</Length \d+/Type/Metadata/Subtype/XML>>\nstream\n(.*?)\nendstream", re.S
)
_TRAILER = re.compile(rb"trailer\n<<(.*?)>>\nstartxref\n(\d+)\n%%EOF\s*$", re.S)
_EXTENSIONS = "<pdfaExtension:schemas><rdf:Bag>"


def embed_metadata(pdf: bytes) -> bytes:
    """Declare the attached XML in the PDF's XMP: the `fx:` properties and
    their PDF/A extension schema, which is what makes it a Factur-X document.

    Typst writes the attachment but takes no custom XMP, so the metadata
    stream is replaced by an incremental update: the new object, a one-entry
    cross-reference section and a trailer pointing back at the original. The
    bytes Typst wrote are untouched and the result is as reproducible.

    shortcut: matches the PDF as Typst 0.15 writes it (classic xref table,
    uncompressed XMP), and raises when it does not; use a PDF library if a
    Typst upgrade changes that."""
    meta, trailer = _METADATA.search(pdf), _TRAILER.search(pdf)
    xmp = meta.group(2).decode() if meta else ""
    if not meta or not trailer or _EXTENSIONS not in xmp or "</rdf:RDF>" not in xmp:
        raise ValueError(
            "cannot declare the Factur-X attachment: the PDF is not laid out as "
            "Typst 0.15 writes it — report this with your typst version"
        )
    xmp = xmp.replace(_EXTENSIONS, _EXTENSIONS + _SCHEMA, 1)
    xmp = xmp.replace("</rdf:RDF>", _DESCRIPTION + "</rdf:RDF>", 1).encode()
    number = int(meta.group(1))
    if not pdf.endswith(b"\n"):
        pdf += b"\n"
    offset = len(pdf)
    obj = b"%d 0 obj\n<</Length %d/Type/Metadata/Subtype/XML>>\nstream\n%s\nendstream\nendobj\n" % (
        number,
        len(xmp),
        xmp,
    )
    xref = offset + len(obj)
    return b"".join(
        [
            pdf,
            obj,
            b"xref\n%d 1\n%010d 00000 n \n" % (number, offset),
            b"trailer\n<<%s/Prev %s>>\nstartxref\n%d\n%%%%EOF\n"
            % (trailer.group(1), trailer.group(2), xref),
        ]
    )
