"""Localized labels for the invoice template.

Adding a language = add one entry to `LABELS` with the full key set (a test
enforces key parity, and `Invoice.locale` validates its language subtag against
these keys, so a new language is accepted the moment it is defined here — no
other file to touch; the matching CLDR locale, e.g. `es_ES`, gives Babel the
number/date formatting for free).

The legally load-bearing strings — `terms`, `export_note`, `reverse_charge` —
should be reviewed by a native/legal speaker before an entry ships.
`terms` MUST keep the literal `{days}` placeholder (`render` calls `.format`)."""

LABELS = {
    "de": {
        "invoice": "Rechnung",
        "invoice_no": "Rechnungs-Nr.",
        "date": "Rechnungsdatum",
        "supply": "Leistungsperiode",
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
        "reference": "Referenz",
        "terms": "Zahlbar innert {days} Tagen netto.",
        "export_note": "Nicht der schweizerischen MWST unterliegend: Ort der "
        "Leistung im Ausland (Art. 8 Abs. 1 MWSTG).",
        "reverse_charge": "Reverse Charge — die Steuer ist vom Leistungsempfänger abzurechnen.",
    },
    "en": {
        "invoice": "Invoice",
        "invoice_no": "Invoice no.",
        "date": "Invoice date",
        "supply": "Service period",
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
        "reference": "Reference",
        "terms": "Payable within {days} days net.",
        "export_note": "Not subject to Swiss VAT: place of supply abroad "
        "(Art. 8 para. 1 Swiss VAT Act).",
        "reverse_charge": "Reverse charge — VAT to be accounted for by the recipient.",
    },
    "es": {
        "invoice": "Factura",
        "invoice_no": "N.º de factura",
        "date": "Fecha de factura",
        "supply": "Periodo de servicio",
        "pos": "N.º",
        "description": "Descripción",
        "qty": "Cantidad",
        "unit_price": "Precio",
        "line_total": "Total",
        "subtotal": "Subtotal",
        "vat": "IVA",
        "rounding": "Redondeo",
        "grand_total": "Total a pagar",
        "payment_to": "A pagar",
        "reference": "Referencia",
        "terms": "Pagadero en un plazo de {days} días netos.",
        "export_note": "No sujeto al IVA suizo: lugar de la prestación en el "
        "extranjero (art. 8 apdo. 1 de la Ley suiza del IVA).",
        "reverse_charge": "Inversión del sujeto pasivo — el IVA debe ser "
        "liquidado por el destinatario.",
    },
}


def labels(language: str) -> dict:
    """Label set for `language` (a locale's language subtag). `Invoice.locale`
    is validated at load, so the fallback only guards programmatic callers."""
    return LABELS.get(language, LABELS["de"])
