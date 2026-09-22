"""Tests for quints inbox (inventory/dedup) and quints match (scored matching)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from quints import config, inbox, match, receivables

LEDGER = """
2024-01-01 open Assets:CH:GmbH:Current:UBS:CHF CHF
2024-01-01 open Assets:CH:GmbH:Receivable:Trade
2024-01-01 open Income:CH:GmbH:Consulting:External:Domestic
2024-01-01 open Expenses:CH:GmbH:Marketing:Tools
2024-01-01 open Equity:CH:GmbH:Contributions:Ivan

2026-07-02 * "ACME" "June invoiced" ^ACME202606
  invoice: "ACME202606"
  Assets:CH:GmbH:Receivable:Trade       5059.10 CHF
  Income:CH:GmbH:Consulting:External:Domestic

2026-07-05 * "Pixeltools" "Plus July"
  Expenses:CH:GmbH:Marketing:Tools        40.86 CHF
  Equity:CH:GmbH:Contributions:Ivan

2026-07-06 * "Linked Supplier" "already documented"
  document: "2026-07-06.linked.thing.pdf"
  Expenses:CH:GmbH:Marketing:Tools        10.00 CHF
  Equity:CH:GmbH:Contributions:Ivan
"""


def _repo(tmp_path: Path) -> Path:
    led = tmp_path / "main.bean"
    led.write_text(LEDGER)
    box = tmp_path / "inbox"
    box.mkdir()
    (box / "README.md").write_text("drop documents here")
    (box / "2026-07-05.pixeltools.plus-july.pdf").write_bytes(b"%PDF-pixeltools")
    (box / "2026-07-06.linked.thing.pdf").write_bytes(b"%PDF-linked")
    (box / "scan001.pdf").write_bytes(b"%PDF-already-filed")
    docs = tmp_path / "documents" / "Expenses" / "CH"
    docs.mkdir(parents=True)
    (docs / "2026-06-01.old.receipt.pdf").write_bytes(b"%PDF-already-filed")
    return led


def test_inbox_inventory(tmp_path: Path) -> None:
    docs = inbox.compute(_repo(tmp_path))
    by_name = {d.name: d for d in docs}
    assert len(docs) == 3

    hint = by_name["2026-07-05.pixeltools.plus-july.pdf"]
    assert (hint.date_hint, hint.payee_hint) == ("2026-07-05", "pixeltools")
    assert not hint.duplicate_of and not hint.linked

    assert by_name["2026-07-06.linked.thing.pdf"].linked
    dup = by_name["scan001.pdf"]
    assert dup.duplicate_of is not None
    assert dup.duplicate_of.endswith("2026-06-01.old.receipt.pdf")
    assert dup.date_hint is None


STAGING = """
2026-07-10 * "ACME AG" "QR payment"
  ubs_ref: "X {qrr}"
  Assets:CH:GmbH:Current:UBS:CHF   5059.10 CHF
  Expenses:CH:GmbH:FIXME          -5059.10 CHF

2026-07-06 ! "PIXELTOOLS.AI" "card payment"
  Assets:CH:GmbH:Current:UBS:CHF    -40.86 CHF
  Expenses:CH:GmbH:FIXME             40.86 CHF
"""


def test_match_all_kinds(tmp_path: Path) -> None:
    from quints.invoice.reference import make_qrr

    led = _repo(tmp_path)
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "2026-07-12-ubs.bean").write_text(STAGING.format(qrr=make_qrr("ACME202606")))

    results = match.compute(led, today=date(2026, 7, 12), cfg=config.Config())
    kinds = {m.kind: m for m in results}

    inv = kinds["payment→invoice"]
    assert inv.score == 1.0 and inv.target["invoice"] == "ACME202606"
    assert "reference" in inv.reasons[0]

    box = kinds["draft→inbox"]
    assert box.target["document"] == "2026-07-05.pixeltools.plus-july.pdf"
    assert box.score >= 0.9  # payee contained + 1 day apart

    booked = kinds["inbox→booked"]
    assert booked.source["document"] == "2026-07-05.pixeltools.plus-july.pdf"
    assert booked.target["payee"] == "Pixeltools" and booked.score >= 0.9
    # the already-documented booking must not appear as a target
    assert all(m.target.get("payee") != "Linked Supplier" for m in results)


_COLLIDING = """
2024-01-01 open Assets:CH:GmbH:Current:UBS:CHF CHF
2024-01-01 open Assets:CH:GmbH:Receivable:Trade
2024-01-01 open Income:CH:GmbH:Consulting:External:Domestic

2026-08-31 * "Academy" "August invoiced" ^ACAD202608
  invoice: "ACAD202608"
  Assets:CH:GmbH:Receivable:Trade       1000.00 CHF
  Income:CH:GmbH:Consulting:External:Domestic

2026-08-31 * "Academy" "August extra invoiced" ^ACAD202608B
  invoice: "ACAD202608B"
  Assets:CH:GmbH:Receivable:Trade        500.00 CHF
  Income:CH:GmbH:Consulting:External:Domestic
"""


def _opens(numbers: list[str]) -> list[receivables.OpenInvoice]:
    return [
        receivables.OpenInvoice(
            number=n,
            payee="Academy",
            invoice_date=date(2026, 8, 31),
            currency="CHF",
            open_amount=Decimal("1000.00"),
            age_days=1,
        )
        for n in numbers
    ]


def test_reference_index_keeps_colliding_references_out_of_lookup() -> None:
    from quints.invoice.reference import legacy_qrr, make_qrr, make_scor

    index = match.reference_index(_opens(["ACAD202608", "ACAD202608B"]))
    # Each invoice is findable by number and by either current-scheme reference.
    for number in ("ACAD202608", "ACAD202608B"):
        assert index.by_key[number] == number
        assert index.by_key[make_scor(number)] == number
        assert index.by_key[make_qrr(number)] == number
    # The legacy reference fits both, so it identifies neither.
    legacy = legacy_qrr("ACAD202608")
    assert legacy not in index.by_key
    assert index.ambiguous[legacy] == ("ACAD202608", "ACAD202608B")
    hit = match.find_invoice(index, f"Zahlung {legacy}")
    assert hit is not None and hit.number is None
    assert "cannot tell them apart" in hit.reason


def test_find_invoice_decodes_a_reference_it_never_minted() -> None:
    # The issuer's bank owns the first six digits of the QR reference; a
    # matcher that only compared strings would miss every payment for an
    # issuer who configured a qr_reference_id.
    from quints.invoice.reference import format_reference, make_qrr

    index = match.reference_index(_opens(["ACAD202608", "ACAD202608B"]))
    printed = format_reference("QRR", make_qrr("ACAD202608B", "123456"))
    hit = match.find_invoice(index, f"GUTSCHRIFT {printed} SEPA")
    assert hit is not None and hit.number == "ACAD202608B"


def test_match_reports_why_a_referenced_payment_is_still_unmatched(tmp_path: Path) -> None:
    from quints.invoice.reference import legacy_qrr

    led = tmp_path / "main.bean"
    led.write_text(_COLLIDING)
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "ubs.bean").write_text(
        f'2026-09-10 ! "Academy" "payment {legacy_qrr("ACAD202608")}"\n'
        f"  Assets:CH:GmbH:Current:UBS:CHF   1000.00 CHF\n"
        f"  Expenses:CH:GmbH:FIXME          -1000.00 CHF\n"
    )
    results = match.compute(led, today=date(2026, 9, 10), cfg=config.Config())
    payments = [m for m in results if m.kind == "payment→invoice"]
    # Both colliding invoices are offered as scored candidates, and every one
    # of them says up front why the quoted reference did not decide it.
    assert {m.target["invoice"] for m in payments} == {"ACAD202608", "ACAD202608B"}
    assert all("cannot tell them apart" in m.reasons[0] for m in payments)


def test_match_empty_is_quiet(tmp_path: Path) -> None:
    led = tmp_path / "main.bean"
    led.write_text(LEDGER)
    assert match.compute(led, today=date(2026, 7, 12), cfg=config.Config()) == []


def test_match_skips_draft_without_amount(tmp_path: Path) -> None:
    """A draft whose first posting elides its amount must be skipped, not crash."""
    led = _repo(tmp_path)
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "2026-07-12-ubs.bean").write_text(
        '2026-07-06 ! "PIXELTOOLS.AI" "card payment"\n'
        "  Assets:CH:GmbH:Current:UBS:CHF\n"
        "  Expenses:CH:GmbH:FIXME             40.86 CHF\n"
    )
    results = match.compute(led, today=date(2026, 7, 12), cfg=config.Config())
    # no draft→inbox / payment→invoice candidates from the amount-less draft
    assert all(m.source.get("staging_file") is None for m in results)


# ── payables: the money-out half of the review loop ──────────────────────────

_PAYABLES = """
2024-01-01 open Assets:CH:GmbH:Current:UBS:CHF CHF
2024-01-01 open Liabilities:CH:GmbH:Payable:Trade
2024-01-01 open Expenses:CH:GmbH:Marketing:Tools

2026-07-01 * "Treuhand Muster" "Bookkeeping Q2" ^TM-2026-4711
  bill: "TM-2026-4711"
  due: 2026-07-31
  Expenses:CH:GmbH:Marketing:Tools        480.00 CHF
  Liabilities:CH:GmbH:Payable:Trade

2026-07-03 * "Pixeltools" "Plus July" ^PT-2026-9
  bill: "PT-2026-9"
  Expenses:CH:GmbH:Marketing:Tools         40.86 CHF
  Liabilities:CH:GmbH:Payable:Trade
"""

_PAYMENT = """
2026-07-20 ! "TREUHAND MUSTER AG" "payment order"
  Assets:CH:GmbH:Current:UBS:CHF         -480.00 CHF
  Expenses:CH:GmbH:FIXME                  480.00 CHF
"""


def _payables_repo(tmp_path: Path, ledger: str = _PAYABLES, staging: str = _PAYMENT) -> Path:
    led = tmp_path / "main.bean"
    led.write_text(ledger)
    box = tmp_path / "inbox"
    box.mkdir()
    (box / "2026-07-03.pixeltools.bill.pdf").write_bytes(b"%PDF-pixeltools-bill")
    out = tmp_path / "staging"
    out.mkdir()
    (out / "2026-07-21-ubs.bean").write_text(staging)
    return led


def test_payment_matches_the_only_open_bill_of_that_amount(tmp_path: Path) -> None:
    led = _payables_repo(tmp_path)
    results = match.compute(led, today=date(2026, 7, 21), cfg=config.Config())
    (pay,) = [m for m in results if m.kind == "payment→payable"]
    assert pay.score == 1.0 and pay.target["bill"] == "TM-2026-4711"
    assert pay.target["due"] == "2026-07-31" and pay.target["open"] == "480.00"
    assert pay.reasons == [
        "payee ≈ 1.00",
        "amount equals open 480.00 CHF",
        "paid 19 d after the bill (2026-07-01)",
    ]


def test_inbox_document_matches_a_booked_supplier_bill(tmp_path: Path) -> None:
    led = _payables_repo(tmp_path)
    results = match.compute(led, today=date(2026, 7, 21), cfg=config.Config())
    (doc,) = [m for m in results if m.kind == "inbox→payable"]
    assert doc.source["document"] == "2026-07-03.pixeltools.bill.pdf"
    assert doc.target["bill"] == "PT-2026-9" and doc.score >= 0.9
    # a booked bill is reported as a payable, not twice
    assert not [m for m in results if m.kind == "inbox→booked"]


def test_an_amount_that_fits_two_bills_identifies_neither(tmp_path: Path) -> None:
    led = _payables_repo(
        tmp_path,
        ledger=_PAYABLES
        + """
2026-07-02 * "Treuhand Muster" "Bookkeeping, second engagement" ^TM-2026-4712
  bill: "TM-2026-4712"
  Expenses:CH:GmbH:Marketing:Tools        480.00 CHF
  Liabilities:CH:GmbH:Payable:Trade
""",
    )
    results = match.compute(led, today=date(2026, 7, 21), cfg=config.Config())
    payments = [m for m in results if m.kind == "payment→payable"]
    assert {m.target["bill"] for m in payments} == {"TM-2026-4711", "TM-2026-4712"}
    # Reported, explained — and never presented as a decided match.
    assert all(m.score < 1.0 for m in payments)
    assert all("cannot tell them apart" in "; ".join(m.reasons) for m in payments)


def test_payment_quoting_the_suppliers_reference_matches_on_it(tmp_path: Path) -> None:
    # A creditor reference (SCOR) spells the supplier's own invoice number
    # out, so a part payment quoting it still identifies the bill.
    from quints.invoice.reference import make_scor

    led = _payables_repo(
        tmp_path,
        staging=f"""
2026-07-25 ! "SAMMELZAHLUNG" "e-banking {make_scor("TM-2026-4711")}"
  Assets:CH:GmbH:Current:UBS:CHF         -200.00 CHF
  Expenses:CH:GmbH:FIXME                  200.00 CHF
""",
    )
    results = match.compute(led, today=date(2026, 7, 25), cfg=config.Config())
    (pay,) = [m for m in results if m.kind == "payment→payable"]
    assert pay.score == 1.0 and pay.target["bill"] == "TM-2026-4711"
    assert pay.reasons == ["invoice reference in payment details"]


def test_a_payment_outside_the_window_is_not_a_candidate(tmp_path: Path) -> None:
    led = _payables_repo(
        tmp_path,
        staging=_PAYMENT.replace("2026-07-20", "2026-06-20"),  # before the bill exists
    )
    results = match.compute(led, today=date(2026, 6, 20), cfg=config.Config())
    assert not [m for m in results if m.kind == "payment→payable"]


def test_import_clears_a_payable_it_can_identify(tmp_path: Path) -> None:
    """The draft an importer writes: counter leg on the payables account,
    `bill:` metadata, the link, flagged complete."""
    from beancount.core import data
    from beancount.core.amount import Amount

    from quints import importing
    from quints import ledger as ledger_mod

    led = tmp_path / "main.bean"
    led.write_text(_PAYABLES)
    existing, _errors = ledger_mod.load_entries(led)
    draft = data.Transaction(
        meta={},
        date=date(2026, 7, 20),
        flag="!",
        payee="TREUHAND MUSTER AG",
        narration="payment order",
        tags=frozenset(),
        links=frozenset(),
        postings=[
            data.Posting(
                "Assets:CH:GmbH:Current:UBS:CHF",
                Amount(Decimal("-480.00"), "CHF"),
                None,
                None,
                None,
                None,
            )
        ],
    )
    result = importing.ImportResult(source="test", drafts=[draft])
    importing.match_payables(result, existing, config.Config())
    assert [n for n, _ in result.payable_matches] == ["TM-2026-4711"]
    matched = result.drafts[0]
    assert matched.flag == "*" and matched.meta["bill"] == "TM-2026-4711"
    assert matched.links == frozenset({"TM-2026-4711"})
    assert matched.postings[1].account == "Liabilities:CH:GmbH:Payable:Trade"


def test_import_leaves_an_ambiguous_payment_alone(tmp_path: Path) -> None:
    from beancount.core import data
    from beancount.core.amount import Amount

    from quints import importing
    from quints import ledger as ledger_mod

    led = tmp_path / "main.bean"
    led.write_text(
        _PAYABLES
        + """
2026-07-02 * "Treuhand Muster" "Bookkeeping, second engagement" ^TM-2026-4712
  bill: "TM-2026-4712"
  Expenses:CH:GmbH:Marketing:Tools        480.00 CHF
  Liabilities:CH:GmbH:Payable:Trade
"""
    )
    existing, _errors = ledger_mod.load_entries(led)
    draft = data.Transaction(
        meta={},
        date=date(2026, 7, 20),
        flag="!",
        payee="TREUHAND MUSTER AG",
        narration="payment order",
        tags=frozenset(),
        links=frozenset(),
        postings=[
            data.Posting(
                "Assets:CH:GmbH:Current:UBS:CHF",
                Amount(Decimal("-480.00"), "CHF"),
                None,
                None,
                None,
                None,
            )
        ],
    )
    result = importing.ImportResult(source="test", drafts=[draft])
    importing.match_payables(result, existing, config.Config())
    assert result.payable_matches == []
    assert result.drafts[0].flag == "!"  # still a human's call
