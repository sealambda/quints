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
    document_path,
    load_customers,
    load_invoice,
    money,
)
from quints.invoice.reference import (
    PaymentReference,
    decode_qrr,
    format_reference,
    legacy_qrr,
    make_qrr,
    make_scor,
    payment_reference,
)

ISSUER = Issuer(
    name="Muster GmbH",
    address=["Musterstrasse 1", "3000 Bern"],
    vat_id="CHE-267.359.056 MWST",
    bank={
        "CHF": BankAccount(qr_iban="CH44 3199 9123 0008 8901 2", bic="UBSWCHZH80A"),
        "EUR": BankAccount(
            iban="DE89 3704 0044 0532 0130 00", bic="COBADEFFXXX", bank_name="Commerzbank AG, Köln"
        ),
    },
)


def _domestic() -> Invoice:
    return Invoice(
        number="ACME202606",
        kind="domestic",
        currency="CHF",
        issue_date=date(2026, 7, 2),
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
        issue_date=date(2026, 6, 17),
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


def test_qrr_is_valid_and_reversible():
    from stdnum.ch import esr

    ref = make_qrr("ACME202606")
    assert len(ref) == 27 and esr.is_valid(ref)  # mod-10 recursive check digit
    assert decode_qrr(ref) == "ACME202606"  # without knowing the issuer's prefix
    # The bank-assigned identification owns the first six digits (UBS: BESR-ID).
    prefixed = make_qrr("ACME202606", "123456")
    assert prefixed.startswith("123456") and esr.is_valid(prefixed)
    assert decode_qrr(prefixed) == "ACME202606"
    assert decode_qrr(ref.replace("0", "1", 1)) != "ACME202606"  # wrong check digit → None
    # Spaced the way a bank re-prints it, and as the payment part groups it.
    assert decode_qrr("00 00000 00019 99860 06390 99176") == "INV2026014"


def test_qrr_is_injective_where_the_legacy_scheme_collided():
    # The real numbering that broke the old scheme: every one of these kept
    # only "202608" and rendered as the same reference, so a payment was
    # credited to whichever invoice happened to be indexed last.
    numbers = ["ACAD202608", "AYUN202608", "KEI202608", "ACAD202608B"]
    assert len({legacy_qrr(n) for n in numbers}) == 1  # the bug
    assert len({make_qrr(n) for n in numbers}) == len(numbers)  # fixed
    assert [decode_qrr(make_qrr(n)) for n in numbers] == numbers
    # Leading zeros survive too — "0042" and "42" are different invoices.
    assert make_qrr("0042") != make_qrr("42")


def test_reference_refuses_to_truncate():
    with pytest.raises(ValueError, match="at most 21"):
        make_scor("A" * 22)
    with pytest.raises(ValueError, match="at most 12"):
        make_qrr("INV" + "0" * 12)
    with pytest.raises(ValueError, match="6 digits"):
        make_qrr("INV1", "12345")


def test_references_in_reads_a_reference_followed_by_words():
    """A bank prints the reference inside a sentence. Mod-97-10 accepts about
    one arbitrary string in 97, so a reader that stopped at the first
    verifying candidate would now and then keep a garbage prefix and drop the
    real reference — this is one of the strings where that happens."""
    from quints.invoice.reference import references_in

    scor = make_scor("ACME202606")
    spaced = " ".join(scor[i : i + 4] for i in range(0, len(scor), 4))
    for text in (
        f"ref {scor} ZAHLUNG ERHALTEN",
        f"Gutschrift {spaced} vielen dank",
        f"{scor}",
    ):
        assert scor in references_in(text), text
    # A 27-digit QR reference is read the same way, however it is grouped —
    # and with its leading zeros stripped, as some statement exports print it.
    qrr = make_qrr("ACME202606")
    grouped = format_reference("QRR", qrr)
    assert references_in(f"GUTSCHRIFT {grouped} SEPA") == [qrr]
    assert references_in(f"QRR {qrr.lstrip('0')} SEPA") == [qrr]


def test_numbers_in_joins_adjacent_tokens_but_prefers_exact_ones():
    from quints.invoice.reference import numbers_in

    spaced = numbers_in("Rechnung ACAD 202608 beglichen")
    assert "ACAD202608" in spaced
    hyphenated = numbers_in("ACAD-2026-08")
    assert "ACAD202608" in hyphenated
    # Exact tokens come before any join: a stray trailing letter must not turn
    # a payment for ACAD202608 into one for ACAD202608B.
    mixed = numbers_in("ACAD202608 B")
    assert mixed.index("ACAD202608") < mixed.index("ACAD202608B")


def test_make_scor_matches_ig_example():
    # Worked example from the SIX Implementation Guidelines QR-bill (Annex A).
    assert make_scor("539007547034") == "RF18539007547034"
    assert make_scor("INV2026014") == "RF47INV2026014"


def test_scheme_follows_the_one_iban_and_refuses_to_guess_between_two():
    inv = _domestic()
    only_iban = BankAccount(iban="CH93 0076 2011 6238 5295 7")
    assert payment_reference(inv, only_iban) == PaymentReference(
        "SCOR", "RF46ACME202606", "RF46 ACME 2026 06"
    )
    # A QR-IBAN on its own can only be paid by QRR.
    only_qr = BankAccount(qr_iban="CH44 3199 9123 0008 8901 2")
    assert payment_reference(inv, only_qr).kind == "QRR"
    # Both configured: the choice decides which account is paid and must be
    # explicit — the previous scaffold wrote exactly this shape, and silently
    # flipping it would re-render old invoices with a different reference.
    both = {"qr_iban": "CH44 3199 9123 0008 8901 2", "iban": "CH93 0076 2011 6238 5295 7"}
    with pytest.raises(ValueError, match="both `iban` and `qr_iban` but no `reference:`"):
        payment_reference(inv, BankAccount.model_validate(both))
    scor = BankAccount.model_validate({**both, "reference": "scor"})
    assert payment_reference(inv, scor).kind == "SCOR"
    qrr = BankAccount.model_validate({**both, "reference": "qrr"})
    assert payment_reference(inv, qrr).kind == "QRR"
    # An export invoice never asks: a credit transfer is SCOR whatever the account.
    export = _domestic()
    export.kind = "export"
    assert payment_reference(export, BankAccount.model_validate(both)).kind == "SCOR"
    # …and asking for QRR without one is refused, with the way out named.
    with pytest.raises(ValueError, match="no `qr_iban`"):
        qr.build_bill(
            inv,
            ISSUER,
            BankAccount(iban="CH93 0076 2011 6238 5295 7", reference="qrr"),
            compute(inv),
        )
    assert payment_reference(export, only_qr).kind == "SCOR"


def test_invoice_number_must_yield_a_reference():
    # Validated at load with the same rule the references are built from:
    # ASCII letters and digits only, and short enough for a SCOR reference.
    with pytest.raises(ValueError, match="no ASCII letter or digit"):
        _invoice(number="ÄÖ-–")
    with pytest.raises(ValueError, match="at most 21"):
        _invoice(number="INV" + "0" * 19)
    assert _invoice(number="INV-2026/014").number == "INV-2026/014"  # punctuation is fine


def test_manual_reference_is_validated_against_the_scheme(tmp_path: Path):
    base = (
        "number: X1\nkind: domestic\ncurrency: CHF\nissue_date: 2026-07-02\n"
        "customer: {name: A, address: [B]}\n"
        "items:\n  - {description: Work, quantity: 1, unit_price: 100}\n"
    )
    (tmp_path / "ok.yaml").write_text(base + "reference: RF18 5390 0754 7034\n")
    inv = load_invoice(tmp_path / "ok.yaml")
    assert inv.reference == "RF18539007547034"  # kept in its compact form
    (tmp_path / "bad.yaml").write_text(base + "reference: RF17 5390 0754 7034\n")
    with pytest.raises(ValueError, match="not a valid SCOR"):
        load_invoice(tmp_path / "bad.yaml")
    (tmp_path / "nonsense.yaml").write_text(base + "reference: ACME/2026\n")
    with pytest.raises(ValueError, match="neither a QR reference"):
        load_invoice(tmp_path / "nonsense.yaml")
    # A QRR override on an account that pays by SCOR is a mismatch, not a coin flip.
    (tmp_path / "qrr.yaml").write_text(base + "reference: 21 00000 00003 13947 14300 09017\n")
    with pytest.raises(ValueError, match="paid with a SCOR reference"):
        payment_reference(
            load_invoice(tmp_path / "qrr.yaml"), BankAccount(iban="CH93 0076 2011 6238 5295 7")
        )


def test_yaml_integers_are_accepted_only_at_full_length():
    """`qr_reference_id: 123456` unquoted is a YAML int and loses nothing;
    `012345` unquoted is octal 5349 by the time pydantic sees it, so the only
    honest answer is to ask for quotes — never to zero-fill into a wrong id."""
    acct = BankAccount.model_validate(
        {"iban": "CH93 0076 2011 6238 5295 7", "qr_reference_id": 123456}
    )
    assert acct.qr_reference_id == "123456"
    with pytest.raises(ValueError, match="Quote it"):
        BankAccount.model_validate({"iban": "CH93 0076 2011 6238 5295 7", "qr_reference_id": 5349})
    # Same rule for a QR reference set by hand: 27 digits or quotes.
    full = make_qrr("ACME202606", "123456")
    assert _invoice(reference=int(full)).reference == full
    with pytest.raises(ValueError, match="Quote it"):
        _invoice(reference=2026085)


def test_qr_payload_structure():
    # This issuer configured a QR-IBAN and nothing else, so it pays by QRR.
    inv = _domestic()
    t = compute(inv)
    lines = qr.payload(qr.build_bill(inv, ISSUER, ISSUER.account("CHF"), t)).splitlines()
    assert lines[0] == "SPC" and lines[-1].startswith("//S1")
    assert lines[3] == "CH4431999123000889012"
    assert "QRR" in lines and "CHF" in lines
    assert make_qrr("ACME202606") in lines
    assert lines[lines.index("EPD") - 1] == "ACME202606"  # unstructured message


def test_qr_payload_carries_swico_billing_information():
    inv = _domestic()
    inv.customer_reference = "PO-4711"
    inv.terms_days = 30
    payload = qr.payload(qr.build_bill(inv, ISSUER, ISSUER.account("CHF"), compute(inv)))
    # Tags ascending, each once; /30/ is the issuer's UID digits only, /32/ the
    # rate on the whole invoice, /40/ net 30 days. No /31/: `supply` is free text.
    assert payload.splitlines()[-1] == (
        "//S1/10/ACME202606/11/260702/20/PO-4711/30/267359056/32/8.1/40/0:30"
    )
    # A slash in a value is escaped in the payload, the way Swico's own
    # example writes it (`/10/X.66711\/8824`).
    inv.customer_reference = "MW/2020/04"
    inv.terms_days = None  # no payment conditions → no /40/ at all
    payload = qr.payload(qr.build_bill(inv, ISSUER, ISSUER.account("CHF"), compute(inv)))
    assert payload.splitlines()[-1] == (
        "//S1/10/ACME202606/11/260702/20/MW\\/2020\\/04/30/267359056/32/8.1"
    )


def test_swico_escapes_and_fits_the_140_character_budget():
    from quints.invoice import swico

    assert swico.escape(r"X.66711/8824") == r"X.66711\/8824"  # the Swico example
    assert swico.escape("a\\b") == "a\\\\b"
    inv = _domestic()
    # Payment conditions, VAT details and the issuer's UID go first; the
    # customer's reference — what their side matches on — survives them all.
    inv.customer_reference = "P" * 90
    built = swico.billing_information(inv, ISSUER, compute(inv), inv.number)
    assert built is not None and built == f"//S1/10/ACME202606/11/260702/20/{'P' * 90}"
    assert len(built) + len(inv.number) <= swico.MAX_LENGTH
    # Only when even that does not fit is it dropped — and then not silently.
    inv.customer_reference = "P" * 120
    with pytest.warns(UserWarning, match="no room left for customer_reference"):
        built = swico.billing_information(inv, ISSUER, compute(inv), inv.number)
    assert built == "//S1/10/ACME202606/11/260702"


def test_export_invoice_prints_a_scor_reference():
    from quints.invoice import render as r

    inv = Invoice(
        number="GLOBEX202608",
        kind="export",
        currency="EUR",
        issue_date=date(2026, 8, 5),
        customer=Party(
            name="Globex Ltd",
            address=["1 Liffey Street", "Dublin 1"],
            country="IE",
            vat_id="IE1234567T",
        ),
        items=[LineItem(description="Work", quantity=Decimal("1"), unit_price=Decimal("500"))],
        locale="en",
        customer_reference="PO-99",
        references=[{"label": "Leitweg-ID", "value": "991-12345-67"}],  # type: ignore[list-item]
    )
    ctx = r.build_context(
        inv,
        ISSUER,
        compute(inv),
        {"reference": payment_reference(inv, ISSUER.account("EUR")).formatted},
    )
    assert ctx["payment"]["reference"] == "RF35 GLOB EX20 2608"
    assert ctx["references"] == [
        {"label": "Your reference", "value": "PO-99"},
        {"label": "Leitweg-ID", "value": "991-12345-67"},
    ]


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


def test_load_invoice_resolves_customer_ref(tmp_path: Path):
    (tmp_path / "customers.yaml").write_text(
        "acme:\n  name: ACME AG\n  address: [Bahnhofstrasse 1, 8000 Zürich]\n"
    )
    (tmp_path / "inv.yaml").write_text(
        "number: X1\nkind: domestic\ncurrency: CHF\nissue_date: 2026-07-02\n"
        "customer: acme\nitems:\n  - {description: Work, quantity: 1, unit_price: 100}\n"
    )
    reg = load_customers(tmp_path / "customers.yaml")
    inv = load_invoice(tmp_path / "inv.yaml", reg)
    assert isinstance(inv.customer, Party)
    assert inv.customer.name == "ACME AG"
    assert inv.customer_slug == "acme"  # the registry key, not a slug of the name
    with pytest.raises(ValueError, match="no customer registry"):
        load_invoice(tmp_path / "inv.yaml", None)


def test_customer_slug_falls_back_to_name(tmp_path: Path):
    # Inline Party (no registry key): slugified name, non-ASCII stripped.
    inv = _domestic()
    assert inv.customer_slug == "acme-ag"
    # Registry key survives even when the name would slug badly (umlauts drop).
    (tmp_path / "customers.yaml").write_text(
        "keinois:\n  name: keinois OÜ\n  address: [Sepapaja tn 6, 15551 Tallinn]\n  country: EE\n"
    )
    (tmp_path / "inv.yaml").write_text(
        "number: KEI202601\nkind: export\ncurrency: EUR\nissue_date: 2026-07-02\n"
        "customer: keinois\nitems:\n  - {description: Work, quantity: 1, unit_price: 100}\n"
    )
    inv = load_invoice(tmp_path / "inv.yaml", load_customers(tmp_path / "customers.yaml"))
    assert inv.customer_slug == "keinois"
    assert document_path(inv, "Income:Export").name == "2026-07-02.keinois.KEI202601.pdf"


def test_load_invoice_toml(tmp_path: Path):
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


def test_draft_quotes_customer_text_safely():
    from beancount.parser import parser as raw_parser

    inv = _domestic()
    inv.customer_reference = 'PO "Phase 2" \\ final'
    text = draft.build_draft(inv, compute(inv), config.Config())
    entries, errors, _ = raw_parser.parse_string(text)
    assert not errors and len(entries) == 1
    assert entries[0].meta["customer_reference"] == 'PO "Phase 2" \\ final'


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
    # Income is the first posting, and the document name matches where the
    # rendered PDF is actually filed (customer slug + invoice number).
    assert text.index(cfg.income_domestic) < text.index(cfg.receivable)
    assert 'document: "2026-07-02.acme-ag.ACME202606.pdf"' in text


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


def test_cross_check_ignores_payment_leg(tmp_path: Path):
    led = tmp_path / "m.bean"
    led.write_text(LEDGER)
    cc = verify.cross_check(led, _domestic(), compute(_domestic()))
    assert cc.found and cc.ok and cc.ledger_total == Decimal("5059.10")
    assert cc.date == "2026-07-02" and cc.date_ok


def test_cross_check_flags_conflict(tmp_path: Path):
    led = tmp_path / "m.bean"
    led.write_text(LEDGER)
    inv = _domestic()
    inv.items[0].unit_price = Decimal("1000.00")
    cc = verify.cross_check(led, inv, compute(inv))
    assert cc.found and not cc.ok


def test_cross_check_missing(tmp_path: Path):
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


def _export(**overrides: object) -> Invoice:
    kw: dict[str, object] = {
        "number": "KEI202605",
        "kind": "export",
        "currency": "EUR",
        "issue_date": date(2026, 6, 17),
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
    return Invoice.model_validate(kw)


def test_reverse_charge_requires_customer_vat(tmp_path: Path):
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


# ── brand tokens ──────────────────────────────────────────────────────────────


def test_brand_tokens_reach_the_template_context():
    from quints.invoice.model import Brand
    from quints.invoice.render import build_context

    brand_in = Brand(accent="#6b1f4a", ink="#1c1618", font="Geist", font_mono="Geist Mono")
    inv = _export()
    brand = build_context(inv, ISSUER.model_copy(update={"brand": brand_in}), compute(inv), {})[
        "brand"
    ]
    assert brand["accent"] == "#6b1f4a"
    assert brand["ink"] == "#1c1618"
    assert brand["font_mono"] == "Geist Mono"
    # Every token the template dereferences must be present: Typst fails on a
    # missing dictionary key, and only when that branch of the layout renders.
    assert {
        "subtle",
        "rule",
        "panel",
        "font_display",
        "display_stretch",
        "logo_height",
    } <= set(brand)


def test_brand_font_families_fall_back_to_the_body_face():
    # Explicitly nulled display/mono families follow the body face; the
    # defaults themselves are the bundled brand (Newsreader / Geist Mono).
    from quints.invoice.model import Brand
    from quints.invoice.render import build_context

    issuer = ISSUER.model_copy(
        update={"brand": Brand(font="Mona Sans", font_display=None, font_mono=None)}
    )
    inv = _export()
    brand = build_context(inv, issuer, compute(inv), {})["brand"]
    assert brand["font_display"] == "Mona Sans"
    assert brand["font_mono"] == "Mona Sans"


def test_brand_defaults_are_the_bundled_families():
    # An unconfigured issuer must never depend on machine-installed fonts:
    # every default family ships with the package.
    from quints.invoice.model import Brand
    from quints.invoice.render import BUNDLED_FAMILIES

    b = Brand()
    assert {b.font, b.font_display, b.font_mono} <= BUNDLED_FAMILIES


def test_brand_rejects_a_non_hex_colour():
    from quints.invoice.model import Brand

    for bad in ("tyrian", "#6b1f4", "6b1f4a", "rgb(107,31,74)"):
        with pytest.raises(ValueError):
            Brand(accent=bad)


def test_brand_accepts_every_hex_form_typst_takes():
    """`accent` was an unconstrained `str` handed straight to Typst's `rgb()`.
    Validating it must not reject a shorthand palette that rendered before."""
    from quints.invoice.model import Brand

    for good in ("#fff", "#fff8", "#6b1f4a", "#6b1f4aff"):
        assert Brand(accent=good).accent == good


# ── schema ────────────────────────────────────────────────────────────────────


def test_json_schemas_expose_authoring_shape():
    s = Invoice.model_json_schema()
    assert {"number", "kind", "currency", "issue_date", "customer", "items"} <= set(s["properties"])
    assert Issuer.model_json_schema()["properties"]["bank"]
    assert "additionalProperties" in CustomerRegistry.model_json_schema()


def test_render_produces_pdf(tmp_path: Path):
    from quints.invoice import render

    out = tmp_path / "inv.pdf"
    _path, totals, payload = render.render(_domestic(), ISSUER, out)
    assert out.exists() and out.read_bytes()[:5] == b"%PDF-"
    assert payload is not None  # domestic invoices always carry a QR payload
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


def test_render_embeds_bundled_font(tmp_path: Path):
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


def test_bundled_fallback_font_is_always_on_the_search_path(tmp_path: Path):
    """Liberation Sans ships with the package: it is the default `Brand.font`,
    the template's last-resort fallback, and the only face the QR-bill payment
    part may use that we can distribute (its guidelines allow four)."""
    from quints.invoice import render

    out = tmp_path / "inv.pdf"
    render.render(_domestic(), ISSUER, out)  # ISSUER sets no font_dir
    assert b"LiberationSans" in out.read_bytes()


def test_qr_bill_keeps_a_permitted_face_without_system_fonts(tmp_path: Path):
    """With system fonts off, Arial and Helvetica are unreachable, and the QR-bill
    payment part may use only those two, Frutiger, or Liberation Sans. It must
    land on the bundled Liberation Sans and not fall through to a Typst default.

    The bold cut doubles as the regression test for weight shadowing: the
    payment part sets headings bold, so a real `-Bold` face has to be embedded.
    """
    from quints.invoice import render
    from quints.invoice.model import Brand

    # Pointing font_dir at our own bundle is the smallest issuer that ships
    # fonts, which is what switches machine-installed families off.
    issuer = ISSUER.model_copy(update={"brand": Brand(font_dir=str(render.BUNDLED_FONTS))})
    out = tmp_path / "inv.pdf"
    render.render(_domestic(), issuer, out)
    pdf = out.read_bytes()
    assert b"LiberationSans-Bold" in pdf
    assert b"DejaVu" not in pdf and b"Libertinus" not in pdf


def test_issuer_bundled_fonts_switch_off_machine_fonts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Regression: a variable font installed on the rendering machine shadows
    the bundled (or issuer-shipped) static cuts of the same family, collapsing
    every weight to the variable default. System fonts stay off whenever the
    issuer ships fonts OR names only bundled families (the default brand does);
    only naming a family that neither side ships keeps them on."""
    import typst

    from quints.invoice import render
    from quints.invoice.model import Brand

    seen: list[object] = []

    def spy(_main: str, **kwargs: object) -> None:
        seen.append(kwargs.get("ignore_system_fonts"))
        raise ValueError("stop after capturing the first attempt")

    monkeypatch.setattr(typst, "compile", spy)

    fonts = render.BUNDLED_FONTS  # any real directory will do
    for brand, expected in [
        (Brand(font_dir=str(fonts)), True),
        (Brand(), True),  # default brand = bundled families only
        (Brand(font="Mona Sans"), False),  # machine-installed family, not shipped
    ]:
        seen.clear()
        issuer = ISSUER.model_copy(update={"brand": brand})
        with pytest.raises(ValueError):
            render.render(_domestic(), issuer, tmp_path / "inv.pdf")
        assert seen[0] is expected


# ── bank details: IBAN/BIC ────────────────────────────────────────────────────


def test_bank_account_validates_iban_and_bic():
    acct = BankAccount(iban="de89 3704 0044 0532 0130 00", bic="cobadeffxxx")
    assert (acct.iban, acct.bic) == ("DE89370400440532013000", "COBADEFFXXX")

    with pytest.raises(ValueError, match="not a valid IBAN"):
        BankAccount(iban="DE89 3704 0044 0532 0130 01")  # one digit off — mod-97 fails
    with pytest.raises(ValueError, match="not a valid IBAN"):
        BankAccount(qr_iban="CH44 3199 9123 0008 8901")  # too short for CH
    with pytest.raises(ValueError, match="not a valid BIC"):
        BankAccount(iban="DE89 3704 0044 0532 0130 00", bic="COBADEFFXX")  # 10 chars


def test_export_invoice_refuses_to_render_without_a_bic(tmp_path: Path):
    """The failure this guards: a payer who has to look the BIC up themselves
    can get it wrong, and the transfer comes back."""
    from quints.invoice import render

    no_bic = ISSUER.model_copy(
        update={"bank": {**ISSUER.bank, "EUR": BankAccount(iban="DE89 3704 0044 0532 0130 00")}}
    )
    with pytest.raises(ValueError, match="has no `bic`"):
        render.render(_export(), no_bic, tmp_path / "x.pdf")


def test_export_payment_block_carries_the_full_instruction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Everything a payer retypes into their banking form, on the PDF: no
    field they have to source themselves."""
    from quints.invoice import render

    seen: list[dict[str, str | None]] = []
    real = render.build_context

    def spy(
        inv: Invoice,
        issuer: Issuer,
        totals: object,
        payment: dict[str, str | None],
        *args: object,
        **kwargs: object,
    ) -> object:
        seen.append(payment)
        return real(inv, issuer, totals, payment, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(render, "build_context", spy)
    path, _, qr_payload = render.render(_export(), ISSUER, tmp_path / "x.pdf")
    assert path.exists() and qr_payload is None  # export: no QR part
    assert seen == [
        {
            "type": "sepa",
            "beneficiary": "Muster GmbH",  # no `holder` set → the issuer
            "iban": "DE89 3704 0044 0532 0130 00",
            "bic": "COBADEFFXXX",
            "bank_name": "Commerzbank AG, Köln",
            "reference": "RF12 KEI2 0260 5",  # SCOR of the invoice number, grouped
        }
    ]

    # `holder` overrides it — the name the payer's bank checks the transfer
    # against, when the account is not held under the issuer's own name.
    seen.clear()
    held = ISSUER.model_copy(
        update={
            "bank": {
                **ISSUER.bank,
                "EUR": ISSUER.account("EUR").model_copy(update={"holder": "Muster Holding AG"}),
            }
        }
    )
    render.render(_export(), held, tmp_path / "y.pdf")
    assert seen[0]["beneficiary"] == "Muster Holding AG"


def test_iban_check_reports_instead_of_guessing():
    from quints.invoice import bank

    ok = bank.check("CH44 3199 9123 0008 8901 2", "UBSWCHZH80A")
    assert (ok.ok, ok.country, ok.iid, ok.notes) == (True, "CH", "31999", [])
    assert ok.formatted == "CH44 3199 9123 0008 8901 2"

    # No BIC is a problem to fix at the source — never a guess from the IID.
    missing = bank.check("CH44 3199 9123 0008 8901 2")
    assert not missing.ok and "no BIC" in missing.problems[0]
    assert missing.bic is None
    assert "31999" in missing.notes[0]  # the IID to look it up by, no BIC invented

    bad = bank.check("DE89 3704 0044 0532 0130 01", "COBADEFFXXX")
    assert not bad.ok and "not a valid IBAN" in bad.problems[0]

    # A payment provider legitimately pairs a foreign BIC with a local IBAN:
    # worth a look, not a blocker.
    mismatch = bank.check("CH44 3199 9123 0008 8901 2", "COBADEFFXXX")
    assert mismatch.ok and "BIC country DE" in mismatch.notes[0]
