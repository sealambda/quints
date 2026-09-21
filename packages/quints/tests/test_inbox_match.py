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
