"""Localized labels for the invoice template.

Adding a language = add one entry to `LABELS` with the full key set (a test
enforces key parity, and `Invoice.locale` validates its language subtag against
these keys, so a new language is accepted the moment it is defined here — no
other file to touch; the matching CLDR locale, e.g. `es_ES`, gives Babel the
number/date formatting for free).

The legally load-bearing strings — `terms`, `export_note`, `reverse_charge`,
`proforma_note`, `advance_vat` —
should be reviewed by a native/legal speaker before an entry ships.
`terms` MUST keep the literal `{days}` placeholder, `advance` its `{number}`
and `{date}`, and `advance_vat` its `{vat}` (`render` calls `.format`)."""

LABELS = {
    "de": {
        "invoice": "Rechnung",
        "invoice_no": "Rechnungs-Nr.",
        "date": "Rechnungsdatum",
        "supply": "Leistungsperiode",
        "your_reference": "Ihre Referenz",
        "pos": "Pos.",
        "description": "Bezeichnung",
        "qty": "Menge",
        "unit_price": "Preis",
        "line_total": "Total",
        "subtotal": "Zwischentotal",
        "vat": "MWST",
        "rounding": "Rundung",
        "grand_total": "Rechnungstotal",
        "payment_to": "Zahlbar an",
        "beneficiary": "Begünstigter",
        "bank": "Bank",
        "reference": "Referenz",
        "scan_to_pay": "Mit der Banking-App scannen",
        "terms": "Zahlbar innert {days} Tagen netto.",
        "export_note": "Nicht der schweizerischen MWST unterliegend: Ort der "
        "Leistung im Ausland (Art. 8 Abs. 1 MWSTG).",
        "reverse_charge": "Reverse Charge — die Steuer ist vom Leistungsempfänger abzurechnen.",
        "proforma": "Proforma-Rechnung",
        "proforma_no": "Proforma-Nr.",
        "proforma_date": "Datum",
        "amount_due": "Zahlbar",
        "invoice_total": "Rechnungstotal",
        "advance": "Anzahlung {number} vom {date}",
        "advance_vat": "inkl. MWST {vat}",
        "proforma_note": "Zahlungsaufforderung für eine Anzahlung. Die Rechnung folgt mit "
        "der Leistung und rechnet diese Zahlung samt der darin enthaltenen MWST an.",
    },
    "en": {
        "invoice": "Invoice",
        "invoice_no": "Invoice no.",
        "date": "Invoice date",
        "supply": "Service period",
        "your_reference": "Your reference",
        "pos": "No.",
        "description": "Description",
        "qty": "Qty",
        "unit_price": "Price",
        "line_total": "Total",
        "subtotal": "Subtotal",
        "vat": "VAT",
        "rounding": "Rounding",
        "grand_total": "Total due",
        "payment_to": "Payable to",
        "beneficiary": "Beneficiary",
        "bank": "Bank",
        "reference": "Reference",
        "scan_to_pay": "Scan with your banking app",
        "terms": "Payable within {days} days net.",
        "export_note": "Not subject to Swiss VAT: place of supply abroad "
        "(Art. 8 para. 1 Swiss VAT Act).",
        "reverse_charge": "Reverse charge — VAT to be accounted for by the recipient.",
        "proforma": "Pro forma invoice",
        "proforma_no": "Pro forma no.",
        "proforma_date": "Date",
        "amount_due": "Amount due",
        "invoice_total": "Invoice total",
        "advance": "Advance {number} received {date}",
        "advance_vat": "incl. VAT {vat}",
        "proforma_note": "Request for an advance payment. The invoice follows with the "
        "supply and credits this payment, including the VAT it contains.",
    },
    "es": {
        "invoice": "Factura",
        "invoice_no": "N.º de factura",
        "date": "Fecha de factura",
        "supply": "Periodo de servicio",
        "your_reference": "Su referencia",
        "pos": "N.º",
        "description": "Descripción",
        "qty": "Cantidad",
        "unit_price": "Precio",
        "line_total": "Total",
        "subtotal": "Subtotal",
        "vat": "IVA",
        "rounding": "Redondeo",
        "grand_total": "Total a pagar",
        "payment_to": "A pagar a",
        "beneficiary": "Beneficiario",
        "bank": "Banco",
        "reference": "Referencia",
        "scan_to_pay": "Escanee con la app de su banco",
        "terms": "A pagar en un plazo de {days} días netos.",
        "export_note": "No sujeto al IVA suizo: lugar de la prestación en el "
        "extranjero (art. 8 apdo. 1 de la Ley suiza del IVA).",
        "reverse_charge": "Inversión del sujeto pasivo — el IVA debe ser "
        "liquidado por el destinatario.",
        "proforma": "Factura proforma",
        "proforma_no": "N.º de proforma",
        "proforma_date": "Fecha",
        "amount_due": "Importe a pagar",
        "invoice_total": "Total factura",
        "advance": "Anticipo {number} recibido el {date}",
        "advance_vat": "IVA incl. {vat}",
        "proforma_note": "Solicitud de pago anticipado. La factura se emitirá con la "
        "prestación y descontará este pago, incluido el IVA que contiene.",
    },
}


def labels(language: str) -> dict[str, str]:
    """Label set for `language` (a locale's language subtag). `Invoice.locale`
    is validated at load, so the fallback only guards programmatic callers."""
    return LABELS.get(language, LABELS["de"])
