"""Localized labels for the invoice template.

Adding a language = add one entry to `LABELS` with the full key set (a test
enforces key parity, and `Invoice.locale` validates its language subtag against
these keys, so a new language is accepted the moment it is defined here — no
other file to touch; the matching CLDR locale, e.g. `es_ES`, gives Babel the
number/date formatting for free).

The legally load-bearing strings — `terms`, `export_note`, `reverse_charge`,
`proforma_note` —
should be reviewed by a native/legal speaker before an entry ships.
`terms` MUST keep the literal `{days}` placeholder and `advance` its `{number}`
and `{date}` (`render` calls `.format`)."""

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
        "terms": "Zahlbar innert {days} Tagen netto.",
        "export_note": "Nicht der schweizerischen MWST unterliegend: Ort der "
        "Leistung im Ausland (Art. 8 Abs. 1 MWSTG).",
        "reverse_charge": "Reverse Charge — die Steuer ist vom Leistungsempfänger abzurechnen.",
        "proforma": "Proforma-Rechnung",
        "proforma_no": "Proforma-Nr.",
        "proforma_date": "Datum",
        "amount_due": "Zahlbar",
        "advance": "Anzahlung {number} vom {date}",
        "proforma_note": "Zahlungsaufforderung für eine Anzahlung. Diese Proforma-Rechnung "
        "berechtigt nicht zum Vorsteuerabzug: Die Rechnung folgt mit der Leistung und "
        "rechnet diese Zahlung an.",
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
        "terms": "Payable within {days} days net.",
        "export_note": "Not subject to Swiss VAT: place of supply abroad "
        "(Art. 8 para. 1 Swiss VAT Act).",
        "reverse_charge": "Reverse charge — VAT to be accounted for by the recipient.",
        "proforma": "Pro forma invoice",
        "proforma_no": "Pro forma no.",
        "proforma_date": "Date",
        "amount_due": "Amount due",
        "advance": "Advance {number} received {date}",
        "proforma_note": "Request for an advance payment. This pro forma invoice is no "
        "basis for an input tax deduction: the invoice follows with the supply and "
        "credits this payment.",
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
        "terms": "A pagar en un plazo de {days} días netos.",
        "export_note": "No sujeto al IVA suizo: lugar de la prestación en el "
        "extranjero (art. 8 apdo. 1 de la Ley suiza del IVA).",
        "reverse_charge": "Inversión del sujeto pasivo — el IVA debe ser "
        "liquidado por el destinatario.",
        "proforma": "Factura proforma",
        "proforma_no": "N.º de proforma",
        "proforma_date": "Fecha",
        "amount_due": "Importe a pagar",
        "advance": "Anticipo {number} recibido el {date}",
        "proforma_note": "Solicitud de pago anticipado. Esta factura proforma no da derecho "
        "a deducir el IVA soportado: la factura se emitirá con la prestación y "
        "descontará este pago.",
    },
}


def labels(language: str) -> dict[str, str]:
    """Label set for `language` (a locale's language subtag). `Invoice.locale`
    is validated at load, so the fallback only guards programmatic callers."""
    return LABELS.get(language, LABELS["de"])
