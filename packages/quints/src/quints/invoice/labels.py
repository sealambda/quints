"""Localized labels for the invoice template (German / English)."""

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
        "reverse_charge": "Reverse Charge — die Steuer ist vom Leistungsempfänger "
                          "abzurechnen.",
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
}


def labels(language: str) -> dict:
    return LABELS.get(language, LABELS["de"])
