"""Tests for quints inbox (inventory/dedup) and quints match (scored matching)."""

from datetime import date
from pathlib import Path

from quints import config, inbox, match

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
    from quints.invoice.model import make_qrr

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
