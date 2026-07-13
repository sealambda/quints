"""Tests for invoice computation, QR payload, registry, drafts, and cross-check."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from quints import config
from quints.invoice import draft, qr, verify
from quints.invoice.model import (
    BankAccount,
    CustomerRegistry,
    Invoice,
    Issuer,
    LineItem,
    Party,
    compute,
    load_customers,
    load_invoice,
    make_qrr,
    make_scor,
    money,
    qrr_check_digit,
)

ISSUER = Issuer(
    name="Muster GmbH",
    address=["Musterstrasse 1", "3000 Bern"],
    vat_id="CHE-267.359.056 MWST",
    bank={
        "CHF": BankAccount(qr_iban="CH44 3199 9123 0008 8901 2", bic="UBSWCHZH80A"),
        "EUR": BankAccount(iban="BE00 0000 0000 0000", bic="TRWIBEB1XXX"),
    },
)


def _domestic():
    return Invoice(
        number="ACME202606",
        kind="domestic",
        currency="CHF",
        issue_date="2026-07-02",
        supply="Juni 2026",
        customer=Party(name="ACME AG", address=["Bahnhofstrasse 1", "8000 Zürich"]),
        items=[
            LineItem(
                description="Consulting",
                quantity=Decimal("1"),
                unit_price=Decimal("4680.00"),
                unit="Pauschal",
            )
        ],
    )


def test_money():
    assert money(Decimal("5059.1")) == "5'059.10"  # default de_CH
    assert money(Decimal("5059.1"), "es_ES") == "5.059,10"
    assert money(Decimal("5059.1"), "en") == "5,059.10"


def test_number_is_locale_aware():
    from quints.invoice.model import number

    assert number(Decimal("2.5"), "es_ES") == "2,5"
    assert number(Decimal("8.1"), "de_CH") == "8.1"


def test_compute_domestic_matches_ledger():
    t = compute(_domestic())
    assert t.subtotal == Decimal("4680.00")
    assert t.vat_amount == Decimal("379.08")
    assert t.rounding == Decimal("0.02")  # 5059.08 → 5059.10 (0.05 rounding)
    assert t.grand_total == Decimal("5059.10")


def test_compute_export_no_vat():
    inv = Invoice(
        number="KEI202605",
        kind="export",
        currency="EUR",
        issue_date="2026-06-17",
        supply="May 2026",
        customer=Party(
            name="nordsoft",
            address=["Tornimäe tn 1", "15551 Tallinn"],
            country="EE",
            vat_id="EE102566484",
        ),
        items=[
            LineItem(
                description="Consulting",
                quantity=Decimal("1"),
                unit_price=Decimal("771.16"),
                unit="flat",
            )
        ],
        round_5=False,
    )
    t = compute(inv)
    assert t.vat_amount == Decimal("0") and t.grand_total == Decimal("771.16")


def test_qrr_check_digit():
    ref = make_qrr("ACME202606")
    assert len(ref) == 27
    assert qrr_check_digit(ref) == "0"  # recursive mod-10 over the full number → 0


def test_make_scor_matches_ig_example():
    # Worked example from the SIX Implementation Guidelines QR-bill (Annex A).
    assert make_scor("539007547034") == "RF18539007547034"


def test_qr_payload_structure():
    t = compute(_domestic())
    acct = ISSUER.account("CHF")
    lines = qr.payload(qr.build_bill(_domestic(), ISSUER, acct, t.grand_total)).splitlines()
    assert lines[0] == "SPC" and lines[-1] == "EPD"
    assert lines[3] == "CH4431999123000889012"
    assert "QRR" in lines and "CHF" in lines


# ── customer registry ─────────────────────────────────────────────────────────


def test_registry_flat_and_versioned():
    reg = CustomerRegistry.model_validate(
        {
            "acme": {"name": "ACME AG", "address": ["Bahnhofstrasse 1", "8000 Zürich"]},
            "mover": {
                "versions": [
                    {
                        "valid_from": "2025-01-01",
                        "name": "Mover AG",
                        "address": ["Old Street 1", "8000 Zürich"],
                    },
                    {
                        "valid_from": "2026-06-01",
                        "name": "Mover AG",
                        "address": ["New Street 2", "8400 Winterthur"],
                    },
                ]
            },
        }
    )
    assert reg.resolve("acme", date(2026, 7, 1)).name == "ACME AG"
    assert reg.resolve("mover", date(2026, 5, 31)).address[0] == "Old Street 1"
    assert reg.resolve("mover", date(2026, 6, 1)).address[0] == "New Street 2"
    with pytest.raises(ValueError, match="unknown customer"):
        reg.resolve("nobody", date(2026, 1, 1))
    with pytest.raises(ValueError, match="no customer version valid"):
        reg.resolve("mover", date(2024, 1, 1))


def test_load_invoice_resolves_customer_ref(tmp_path):
    (tmp_path / "customers.yaml").write_text(
        "acme:\n  name: ACME AG\n  address: [Bahnhofstrasse 1, 8000 Zürich]\n"
    )
    (tmp_path / "inv.yaml").write_text(
        "number: X1\nkind: domestic\ncurrency: CHF\nissue_date: 2026-07-02\n"
        "customer: acme\nitems:\n  - {description: Work, quantity: 1, unit_price: 100}\n"
    )
    reg = load_customers(tmp_path / "customers.yaml")
    inv = load_invoice(tmp_path / "inv.yaml", reg)
    assert inv.customer.name == "ACME AG"
    with pytest.raises(ValueError, match="no customer registry"):
        load_invoice(tmp_path / "inv.yaml", None)


def test_load_invoice_toml(tmp_path):
    (tmp_path / "inv.toml").write_text(
        'number = "X2"\nkind = "export"\ncurrency = "EUR"\n'
        'issue_date = 2026-06-17\n\n[customer]\nname = "nordsoft"\n'
        'address = ["Tornimäe tn 1", "15551 Tallinn"]\ncountry = "EE"\n\n'
        '[[items]]\ndescription = "Consulting"\nquantity = 1\nunit_price = 771.16\n'
    )
    inv = load_invoice(tmp_path / "inv.toml")
    assert inv.currency == "EUR" and inv.issue_date == date(2026, 6, 17)
    assert compute(inv).grand_total == Decimal("771.16")


# ── ledger draft + cross-check ────────────────────────────────────────────────


def test_draft_is_balanced_and_complete():
    cfg = config.Config()
    text = draft.build_draft(_domestic(), compute(_domestic()), cfg)
    assert "^ACME202606" in text and 'invoice: "ACME202606"' in text
    amounts = [
        Decimal(tok)
        for line in text.splitlines()
        for tok in line.split()
        if tok.replace("-", "").replace(".", "").isdigit() and "." in tok
    ]
    assert sum(amounts) == Decimal("0")
    assert cfg.receivable in text and cfg.income_domestic in text
    assert cfg.output_vat in text and cfg.rounding_income in text


LEDGER = """
2024-01-01 open Assets:CH:GmbH:Receivable:Trade
2024-01-01 open Assets:CH:GmbH:Current:Wise:EUR
2024-01-01 open Income:CH:GmbH:Consulting:External:Domestic CHF
2024-01-01 open Income:CH:GmbH:Rounding CHF
2024-01-01 open Liabilities:CH:GmbH:Tax:OutputVAT CHF
2026-07-02 * "ACME" "June" ^ACME202606
  Assets:CH:GmbH:Receivable:Trade      5059.10 CHF
  Income:CH:GmbH:Consulting:External:Domestic  -4680.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT     -379.08 CHF
  Income:CH:GmbH:Rounding                 -0.02 CHF
2026-07-20 * "ACME" "June paid" ^ACME202606
  Assets:CH:GmbH:Current:Wise:EUR       4500.00 EUR
  Assets:CH:GmbH:Receivable:Trade      -5059.10 CHF @@ 4500.00 EUR
"""


def test_cross_check_ignores_payment_leg(tmp_path):
    led = tmp_path / "m.bean"
    led.write_text(LEDGER)
    cc = verify.cross_check(led, _domestic(), compute(_domestic()))
    assert cc.found and cc.ok and cc.ledger_total == Decimal("5059.10")
    assert cc.date == "2026-07-02" and cc.date_ok


def test_cross_check_flags_conflict(tmp_path):
    led = tmp_path / "m.bean"
    led.write_text(LEDGER)
    inv = _domestic()
    inv.items[0].unit_price = Decimal("1000.00")
    cc = verify.cross_check(led, inv, compute(inv))
    assert cc.found and not cc.ok


def test_cross_check_missing(tmp_path):
    led = tmp_path / "m.bean"
    led.write_text(LEDGER)
    inv = _domestic()
    inv.number = "ACME209999"
    assert not verify.cross_check(led, inv, compute(inv)).found


# ── VAT-number validation + reverse charge ────────────────────────────────────


def test_vat_id_checksums():
    from quints.invoice import vatid

    vatid.validate("CHE-267.359.056 MWST", "CH")  # issuer UID
    vatid.validate("EE102566484", "EE")  # Estonian KMKR
    vatid.validate("123456788", "US")  # no US VAT regime → accepted
    with pytest.raises(ValueError, match="invalid VAT number"):
        vatid.validate("DE123456789", "DE")  # bad checksum
    with pytest.raises(ValueError, match="invalid VAT number"):
        vatid.validate("CHE-274.485.075 MWST", "CH")  # one digit off


def test_party_rejects_bad_vat_id():
    with pytest.raises(ValueError, match="invalid VAT number"):
        Party(name="Bad AG", address=["Weg 1", "8000 Zürich"], vat_id="CHE-111.111.111 MWST")


def _export(**overrides):
    kw = {
        "number": "KEI202605",
        "kind": "export",
        "currency": "EUR",
        "issue_date": "2026-06-17",
        "customer": Party(
            name="nordsoft",
            address=["Tornimäe tn 1", "15551 Tallinn"],
            country="EE",
            vat_id="EE102566484",
        ),
        "items": [
            LineItem(description="Consulting", quantity=Decimal("1"), unit_price=Decimal("771.16"))
        ],
        "round_5": False,
    }
    kw.update(overrides)
    return Invoice(**kw)


def test_reverse_charge_requires_customer_vat(tmp_path):
    from quints.invoice import render

    no_vat = Party(name="Acme Inc", address=["1 Main St", "94105 San Francisco"], country="US")
    with pytest.raises(ValueError, match="reverse charge"):
        render.render(_export(customer=no_vat), ISSUER, tmp_path / "x.pdf")
    # explicit opt-out for non-reverse-charge jurisdictions renders fine
    path, _, _ = render.render(
        _export(customer=no_vat, reverse_charge=False), ISSUER, tmp_path / "y.pdf"
    )
    assert path.exists()


def test_reverse_charge_flag_in_context():
    from quints.invoice.render import build_context

    inv = _export()
    ctx = build_context(inv, ISSUER, compute(inv), {"type": "sepa"}, reverse_charge=True)
    assert ctx["reverse_charge"] is True


# ── schema ────────────────────────────────────────────────────────────────────


def test_json_schemas_expose_authoring_shape():
    s = Invoice.model_json_schema()
    assert {"number", "kind", "currency", "issue_date", "customer", "items"} <= set(s["properties"])
    assert Issuer.model_json_schema()["properties"]["bank"]
    assert "additionalProperties" in CustomerRegistry.model_json_schema()


def test_render_produces_pdf(tmp_path):
    from quints.invoice import render

    out = tmp_path / "inv.pdf"
    _path, totals, payload = render.render(_domestic(), ISSUER, out)
    assert out.exists() and out.read_bytes()[:5] == b"%PDF-"
    assert payload.startswith("SPC") and totals.grand_total == Decimal("5059.10")


# ── localization ──────────────────────────────────────────────────────────────


def test_labels_all_languages_share_the_same_keys():
    from quints.invoice.labels import LABELS

    reference = set(LABELS["de"])
    for lang, lbl in LABELS.items():
        assert set(lbl) == reference, f"{lang} label keys diverge from de"
        assert "{days}" in lbl["terms"], f"{lang} terms dropped the {{days}} placeholder"


def test_spanish_terms_formats_day_count():
    from quints.invoice.labels import labels

    assert labels("es")["terms"].format(days=30) == "A pagar en un plazo de 30 días netos."


def _invoice(**over: object) -> Invoice:
    # model_validate takes a mapping (not typed kwargs), so a test can pass an
    # invalid key like a legacy `language` and exercise the validators.
    base: dict[str, object] = {
        "number": "X1",
        "kind": "domestic",
        "currency": "CHF",
        "issue_date": date(2026, 7, 2),
        "customer": Party(name="ACME AG", address=["Bahnhofstrasse 1", "8000 Zürich"]),
        "items": [LineItem(description="Work", quantity=Decimal("1"), unit_price=Decimal("100"))],
    }
    base.update(over)
    return Invoice.model_validate(base)


def test_invoice_rejects_unknown_locale():
    with pytest.raises(ValueError, match="unknown locale"):
        _invoice(locale="es_CH")  # not a CLDR locale


def test_invoice_rejects_locale_without_labels():
    with pytest.raises(ValueError, match="no invoice labels"):
        _invoice(locale="fr_FR")  # real locale, no fr labels


def test_invoice_rejects_legacy_language_key():
    with pytest.raises(ValueError, match="`language` is replaced by `locale`"):
        _invoice(language="es")


def test_locale_exposes_label_language():
    assert _invoice(locale="es_ES").language == "es"


def test_render_spanish_formats_numbers_and_dates():
    # es_ES: comma decimal, dotted thousands, Spanish medium date.
    from quints.invoice.model import compute
    from quints.invoice.render import build_context

    inv = _invoice(
        locale="es_ES",
        items=[
            LineItem(description="Trabajo", quantity=Decimal("2.5"), unit_price=Decimal("1000"))
        ],
    )
    ctx = build_context(inv, ISSUER, compute(inv), {"type": "qrbill"})
    assert ctx["invoice"]["issue_date"] == "2 jul 2026"
    assert ctx["items"][0]["quantity"] == "2,5"
    assert ctx["items"][0]["unit_price"] == "1.000,00"
    assert ctx["totals"]["vat_rate"] == "8,1"
    assert ctx["totals"]["grand_total"] == "2.702,50"  # 2500 + 8.1% VAT = 2702.50


def test_render_spanish_from_swiss_issuer(tmp_path: Path):
    from quints.invoice import render

    inv = _domestic()
    inv.locale = "es_ES"
    out = tmp_path / "inv-es.pdf"
    path, _totals, _payload = render.render(inv, ISSUER, out)
    assert path.exists() and out.read_bytes()[:5] == b"%PDF-"


def test_render_embeds_bundled_font(tmp_path):
    from pathlib import Path as P

    import pytest

    from quints.invoice import render
    from quints.invoice.model import Brand

    # Brand fonts are entity assets (ledger repo's invoicing/), not package
    # fixtures — exercise embedding only where they exist.
    font_dir = P(__file__).parents[3] / "invoicing" / "fonts" / "mona-sans"
    if not font_dir.is_dir():
        pytest.skip("brand font assets not available (private ledger repo only)")
    issuer = ISSUER.model_copy(
        update={
            "brand": Brand(
                font="Mona Sans",
                font_display_stretch=125,
                font_dir=str(font_dir),
            )
        }
    )
    out = tmp_path / "inv.pdf"
    render.render(_domestic(), issuer, out)
    pdf = out.read_bytes()
    # body font and the wide title cut must be embedded, not system fallbacks
    assert b"MonaSans-Regular" in pdf
    assert b"MonaSansExpanded-SemiBold" in pdf
