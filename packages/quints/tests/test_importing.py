"""Integration test for `quints import ubs` (fixture statement → staging drafts)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from beancount.core import data
from beancount.core.amount import Amount

from quints import config, importing

FIXTURE = Path(__file__).parent / "fixtures" / "transactions.mt940"

_CFG = config.Config(
    import_ubs=config.UbsImport(
        iban="CH9300762011623852957",
        rules=(
            (r"\bacme\b", "Assets:CH:GmbH:Receivable:Trade", "*"),
            (r"steuerverwaltung|\bestv\b", "Liabilities:CH:GmbH:Tax:PayableVAT", "*"),
            (r"google workspace", "Liabilities:CH:GmbH:Payable:Trade", "*"),
            (r"bkg\*|booking\.com", "Expenses:CH:GmbH:Travel:Transport", "!"),
            (r"pixeltools", "Expenses:CH:GmbH:Marketing:Tools", "!"),
        ),
    ),
    import_wise=config.WiseImport(holder="Muster GmbH"),
)

# One entry already imported (carries ubs_ref) and one legacy entry booked
# before the importer existed (no ref; matched by amount + date window).
_LEDGER = """
2024-01-01 open Assets:CH:GmbH:Current:UBS:CHF CHF
2024-01-01 open Income:CH:GmbH:Consulting:External:Domestic
2024-01-01 open Expenses:CH:GmbH:Marketing:Tools

2026-04-08 * "ACME" "February received"
    Assets:CH:GmbH:Current:UBS:CHF             5691.00 CHF
    Income:CH:GmbH:Consulting:External:Domestic

2026-05-13 * "Pixeltools" "Pixeltools Plus May"
    ubs_ref: "9930635BN7487612"
    Expenses:CH:GmbH:Marketing:Tools             40.86 CHF
    Assets:CH:GmbH:Current:UBS:CHF              -40.86 CHF
"""


def test_run_ubs_dedups_and_stages(tmp_path: Path) -> None:
    ledger_file = tmp_path / "main.bean"
    ledger_file.write_text(_LEDGER)

    result = importing.run_ubs(FIXTURE, ledger_file, out_dir=tmp_path / "staging", cfg=_CFG)

    # 14 non-zero statement entries: 1 deduped by ref, 1 by legacy match.
    assert result.skipped_ref == 1
    assert len(result.legacy_matches) == 1
    assert result.legacy_matches[0][0].payee == "Acme AG"
    assert len(result.drafts) == 12

    assert len(result.balances) == 1
    assert result.balances[0].amount.number == Decimal("30407.14")

    assert result.out_path is not None
    staged = result.out_path.read_text()
    assert 'ubs_ref: "9902063AR6387321"' in staged  # share-capital draft
    assert 'ubs_ref: "9930635BN7487612"' not in staged  # already imported
    assert "2027-01-01 balance Assets:CH:GmbH:Current:UBS:CHF" in staged

    # Re-running against a ledger containing the staged refs would skip them;
    # against the same ledger the result is identical (idempotent).
    again = importing.run_ubs(FIXTURE, ledger_file, out_dir=tmp_path / "staging", cfg=_CFG)
    assert len(again.drafts) == len(result.drafts)


_WISE_EUR = """{
  "accountHolder": {"type": "BUSINESS", "businessName": "Muster GmbH"},
  "transactions": [
    {"type": "DEBIT", "date": "2026-07-04T10:00:00.000Z",
     "amount": {"value": -100.00, "currency": "EUR"},
     "totalFees": {"value": 0.00, "currency": "EUR"},
     "details": {"type": "TRANSFER", "description": "Sent money to John Doe",
                 "recipient": {"name": "John Doe"}},
     "referenceNumber": "TRANSFER-71"},
    {"type": "DEBIT", "date": "2026-07-08T08:30:00.000Z",
     "amount": {"value": -50.00, "currency": "EUR"},
     "totalFees": {"value": 0.24, "currency": "EUR"},
     "details": {"type": "CONVERSION", "description": "Converted 50.00 EUR to 53.60 USD"},
     "referenceNumber": "CONVERSION-72"}
  ],
  "endOfStatementBalance": {"value": 6395.74, "currency": "EUR"},
  "query": {"intervalStart": "2026-07-01T00:00:00.000Z",
            "intervalEnd": "2026-07-10T23:59:59.999Z", "currency": "EUR"}
}"""

_WISE_USD = """{
  "accountHolder": {"type": "BUSINESS", "businessName": "Muster GmbH"},
  "transactions": [
    {"type": "CREDIT", "date": "2026-07-08T08:30:00.000Z",
     "amount": {"value": 53.60, "currency": "USD"},
     "totalFees": {"value": 0.00, "currency": "USD"},
     "details": {"type": "CONVERSION", "description": "Converted 50.00 EUR to 53.60 USD"},
     "referenceNumber": "CONVERSION-72"}
  ],
  "endOfStatementBalance": {"value": 53.60, "currency": "USD"},
  "query": {"intervalStart": "2026-07-01T00:00:00.000Z",
            "intervalEnd": "2026-07-10T23:59:59.999Z", "currency": "USD"}
}"""


def test_run_wise_merges_conversions_and_dedups(tmp_path: Path) -> None:
    ledger_file = tmp_path / "main.bean"
    ledger_file.write_text(
        "2024-01-01 open Assets:CH:GmbH:Current:Wise:EUR EUR\n"
        "2024-01-01 open Assets:CH:GmbH:Current:Wise:USD USD\n"
        "2024-01-01 open Expenses:CH:GmbH:Education:Marketing\n"
        "\n"
        '2026-07-04 * "John Doe" "July"\n'
        "    Expenses:CH:GmbH:Education:Marketing               100.00 EUR\n"
        "    Assets:CH:GmbH:Current:Wise:EUR                   -100.00 EUR\n"
    )
    eur = tmp_path / "wise-eur.json"
    usd = tmp_path / "wise-usd.json"
    eur.write_text(_WISE_EUR)
    usd.write_text(_WISE_USD)

    result = importing.run_wise([eur, usd], ledger_file, out_dir=tmp_path / "staging", cfg=_CFG)

    # John Doe transfer matches the already-booked payment by amount+date.
    assert len(result.legacy_matches) == 1
    assert result.legacy_matches[0][0].payee == "John Doe"

    # The conversion legs merged into one two-currency draft with fee split.
    assert len(result.drafts) == 1
    conversion = result.drafts[0]
    accounts = [p.account for p in conversion.postings]
    assert accounts == [
        "Assets:CH:GmbH:Current:Wise:EUR",
        "Assets:CH:GmbH:Current:Wise:USD",
        "Expenses:CH:GmbH:BankFees:Wise",
    ]
    price = conversion.postings[1].price
    assert price is not None
    assert price.number == Decimal("49.76")  # 50.00 − 0.24 fee

    # One closing-balance assertion per currency file.
    assert len(result.balances) == 2
    assert result.out_path is not None
    staged = result.out_path.read_text()
    assert "2026-07-11 balance Assets:CH:GmbH:Current:Wise:EUR" in staged
    assert "2026-07-11 balance Assets:CH:GmbH:Current:Wise:USD" in staged


def test_rules_draft_counter_legs(tmp_path: Path) -> None:
    ledger_file = tmp_path / "main.bean"
    ledger_file.write_text("2024-01-01 open Assets:CH:GmbH:Current:UBS:CHF CHF\n")

    result = importing.run_ubs(FIXTURE, ledger_file, out_dir=tmp_path / "staging", cfg=_CFG)
    by_payee = {d.payee: d for d in result.drafts}

    estv = by_payee["Eidgen÷ssische Steuerverwaltung"]
    assert estv.flag == "*"
    assert estv.postings[1].account == "Liabilities:CH:GmbH:Tax:PayableVAT"

    flight = by_payee["BKG*BOOKING.COM FLIGHT"]
    assert flight.flag == "!"  # direct expense: VAT + document still to decide
    assert flight.postings[1].account == "Expenses:CH:GmbH:Travel:Transport"

    capital = by_payee["Muster GmbH"]
    assert capital.flag == "!" and len(capital.postings) == 1


# ── QR-reference → open-invoice matching ──────────────────────────────────────

_RECV_LEDGER = """
2024-01-01 open Assets:CH:GmbH:Current:UBS:CHF CHF
2024-01-01 open Assets:CH:GmbH:Receivable:Trade
2024-01-01 open Income:CH:GmbH:Consulting:External:Domestic

2026-07-02 * "ACME" "June invoiced" ^ACME202606
    invoice: "ACME202606"
    Assets:CH:GmbH:Receivable:Trade            5059.10 CHF
    Income:CH:GmbH:Consulting:External:Domestic
"""


def _draft(
    payee: str, narration: str, amount: str, meta: dict[str, str] | None = None
) -> data.Transaction:
    return data.Transaction(
        meta=dict(meta or {}),
        date=date(2026, 7, 20),
        flag="!",
        payee=payee,
        narration=narration,
        tags=frozenset(),
        links=frozenset(),
        postings=[
            data.Posting(
                "Assets:CH:GmbH:Current:UBS:CHF",
                Amount(Decimal(amount), "CHF"),
                None,
                None,
                None,
                None,
            ),
            data.Posting(
                "Expenses:CH:GmbH:FIXME", Amount(-Decimal(amount), "CHF"), None, None, None, None
            ),
        ],
    )


def test_match_receivables_by_qrr_and_scor() -> None:
    from beancount.loader import load_string

    from quints import config
    from quints.invoice.reference import make_qrr, make_scor

    entries, _, _ = load_string(_RECV_LEDGER)
    qrr = make_qrr("ACME202606")
    spaced_rf = " ".join([make_scor("ACME202606")[i : i + 4] for i in range(0, 30, 4)])

    result = importing.ImportResult(source="test")
    result.drafts = [
        _draft("ACME AG", "payment", "5059.10", {"ubs_ref": f"X1 {qrr}"}),
        _draft("ACME AG", f"ref {spaced_rf} paid thanks", "1000.00"),
        _draft("ACME AG", f"refund {qrr}", "-50.00"),  # outgoing → untouched
        _draft("Somebody", "unrelated", "12.00"),
    ]
    importing.match_receivables(result, entries, config.Config())

    assert [n for n, _ in result.receivable_matches] == ["ACME202606", "ACME202606"]
    matched = result.drafts[0]
    assert matched.flag == "*"
    assert matched.postings[1].account == "Assets:CH:GmbH:Receivable:Trade"
    assert "ACME202606" in matched.links
    assert matched.meta["invoice"] == "ACME202606"
    assert result.drafts[2].flag == "!"  # outgoing untouched
    assert result.drafts[3].postings[1].account == "Expenses:CH:GmbH:FIXME"


_SUFFIX_LEDGER = """
2024-01-01 open Assets:CH:GmbH:Current:UBS:CHF CHF
2024-01-01 open Assets:CH:GmbH:Receivable:Trade
2024-01-01 open Income:CH:GmbH:Consulting:External:Domestic

2026-08-31 * "Academy" "August invoiced" ^ACAD202608
    invoice: "ACAD202608"
    Assets:CH:GmbH:Receivable:Trade            1000.00 CHF
    Income:CH:GmbH:Consulting:External:Domestic

2026-08-31 * "Academy" "August extra invoiced" ^ACAD202608B
    invoice: "ACAD202608B"
    Assets:CH:GmbH:Receivable:Trade             500.00 CHF
    Income:CH:GmbH:Consulting:External:Domestic
"""


def test_match_receivables_does_not_confuse_a_suffixed_invoice_number() -> None:
    """The failure this rewrite exists for: `ACAD202608` is a substring of
    `ACAD202608B`, so a substring search credited the suffixed invoice's
    payment — and its correct SCOR reference — to the shorter invoice."""
    from beancount.loader import load_string

    from quints import config
    from quints.invoice.reference import make_scor

    entries, _, _ = load_string(_SUFFIX_LEDGER)
    scor_b = make_scor("ACAD202608B")
    spaced = " ".join(scor_b[i : i + 4] for i in range(0, len(scor_b), 4))

    result = importing.ImportResult(source="test")
    result.drafts = [
        _draft("Academy", "Rechnung ACAD202608B beglichen", "500.00"),
        _draft("Academy", f"ref {spaced} vielen dank", "500.00"),
        _draft("Academy", "Rechnung ACAD202608 beglichen", "1000.00"),
        _draft("Academy", f"ref {make_scor('ACAD202608')}", "1000.00"),
        _draft("Academy", "Rechnung ACAD 202608 beglichen", "1000.00"),  # typed with a space
    ]
    importing.match_receivables(result, entries, config.Config())
    assert [n for n, _ in result.receivable_matches] == [
        "ACAD202608B",
        "ACAD202608B",
        "ACAD202608",
        "ACAD202608",
        "ACAD202608",
    ]


def test_match_receivables_still_clears_a_legacy_qrr_payment() -> None:
    """Invoices already in customers' hands carry the old zero-padded QR
    reference; their payments still have to find their invoice."""
    from beancount.loader import load_string

    from quints import config
    from quints.invoice.reference import legacy_qrr

    entries, _, _ = load_string(_RECV_LEDGER)
    result = importing.ImportResult(source="test")
    result.drafts = [_draft("ACME AG", "payment", "5059.10", {"ubs_ref": legacy_qrr("ACME202606")})]
    importing.match_receivables(result, entries, config.Config())
    assert [n for n, _ in result.receivable_matches] == ["ACME202606"]


def test_match_receivables_refuses_an_ambiguous_legacy_reference() -> None:
    """Two invoices whose legacy references collide identify neither: the
    draft stays flagged for a human instead of being credited to one."""
    from beancount.loader import load_string

    from quints import config
    from quints.invoice.reference import legacy_qrr

    entries, _, _ = load_string(_SUFFIX_LEDGER)
    assert legacy_qrr("ACAD202608") == legacy_qrr("ACAD202608B")
    result = importing.ImportResult(source="test")
    result.drafts = [_draft("Academy", "payment", "500.00", {"ubs_ref": legacy_qrr("ACAD202608")})]
    importing.match_receivables(result, entries, config.Config())
    assert result.receivable_matches == []
    assert result.drafts[0].flag == "!"


def test_match_receivables_completes_a_cash_only_draft() -> None:
    # Importers with no rule match (e.g. yapeal's default row) draft a
    # single-posting transaction — only the cash leg. A match must add the
    # elided receivable posting, not just flip the flag, or the "matched"
    # draft fails beancount's balance check once moved into books/.
    from beancount.loader import load_string
    from beancount.parser import printer

    from quints import config
    from quints.invoice.reference import make_qrr

    entries, _, _ = load_string(_RECV_LEDGER)
    qrr = make_qrr("ACME202606")

    result = importing.ImportResult(source="test")
    result.drafts = [
        data.Transaction(
            meta={"ubs_ref": f"X1 {qrr}"},
            date=date(2026, 7, 20),
            flag="!",
            payee="ACME AG",
            narration="payment",
            tags=frozenset(),
            links=frozenset(),
            postings=[
                data.Posting(
                    "Assets:CH:GmbH:Current:UBS:CHF",
                    Amount(Decimal("5059.10"), "CHF"),
                    None,
                    None,
                    None,
                    None,
                ),
            ],
        )
    ]
    importing.match_receivables(result, entries, config.Config())

    matched = result.drafts[0]
    assert matched.flag == "*"
    assert len(matched.postings) == 2
    assert matched.postings[1].account == "Assets:CH:GmbH:Receivable:Trade"
    assert matched.postings[1].units is None  # elided — auto-balances the cash leg

    booked = _RECV_LEDGER + "\n" + printer.format_entry(matched)
    _entries, errors, _ = load_string(booked)
    assert not errors, errors


_VAT_LEDGER = """
2024-01-01 open Assets:CH:GmbH:Current:UBS:CHF CHF
2024-01-01 open Liabilities:CH:GmbH:Tax:PayableVAT CHF
2024-01-01 open Liabilities:CH:GmbH:Tax:OutputVAT CHF

2026-03-31 * "2026-Q1 VAT Settlement" ^VAT-2026-Q1
    due: 2026-05-30
    Liabilities:CH:GmbH:Tax:PayableVAT   -300.00 CHF
    Liabilities:CH:GmbH:Tax:OutputVAT     300.00 CHF

2026-06-30 * "2026-Q2 VAT Settlement" ^VAT-2026-Q2
    due: 2026-08-29
    Liabilities:CH:GmbH:Tax:PayableVAT   -419.73 CHF
    Liabilities:CH:GmbH:Tax:OutputVAT     419.73 CHF
"""


def _vat_draft(amount: str) -> data.Transaction:
    d = _draft("Eidg. Steuerverwaltung", "MWST", amount)
    return d._replace(
        flag="*",
        postings=[
            d.postings[0],
            d.postings[1]._replace(account="Liabilities:CH:GmbH:Tax:PayableVAT"),
        ],
    )


def test_match_vat_links_the_payment_to_the_period_it_clears() -> None:
    from beancount.loader import load_string

    entries, _, _ = load_string(_VAT_LEDGER)
    result = importing.ImportResult(source="test")
    result.drafts = [
        _vat_draft("-419.73"),  # clears Q2 exactly
        _vat_draft("-100.00"),  # a part payment identifies no period
        _draft("Somebody", "same amount, not routed to PayableVAT", "-300.00"),
    ]
    importing.match_vat(result, entries, config.Config())

    assert [n for n, _ in result.vat_matches] == ["VAT-2026-Q2"]
    assert result.drafts[0].links == {"VAT-2026-Q2"}
    assert not result.drafts[1].links
    assert not result.drafts[2].links and result.drafts[2].flag == "!"


def test_match_vat_refuses_two_periods_owing_the_same_amount() -> None:
    from beancount.loader import load_string

    entries, _, _ = load_string(_VAT_LEDGER.replace("300.00", "419.73"))
    result = importing.ImportResult(source="test")
    result.drafts = [_vat_draft("-419.73")]
    importing.match_vat(result, entries, config.Config())

    assert not result.vat_matches and not result.drafts[0].links


def test_run_ubs_links_the_estv_debit_to_its_period(tmp_path: Path) -> None:
    ledger_file = tmp_path / "main.bean"
    ledger_file.write_text(
        "2024-01-01 open Assets:CH:GmbH:Current:UBS:CHF CHF\n"
        "2024-01-01 open Liabilities:CH:GmbH:Tax:PayableVAT CHF\n"
        "2024-01-01 open Liabilities:CH:GmbH:Tax:OutputVAT CHF\n"
        '2026-03-31 * "2026-Q1 VAT Settlement" ^VAT-2026-Q1\n'
        "    Liabilities:CH:GmbH:Tax:PayableVAT   -398.08 CHF\n"
        "    Liabilities:CH:GmbH:Tax:OutputVAT     398.08 CHF\n"
    )
    result = importing.run_ubs(FIXTURE, ledger_file, out_dir=tmp_path / "staging", cfg=_CFG)

    assert [n for n, _ in result.vat_matches] == ["VAT-2026-Q1"]
    assert result.out_path is not None
    assert "^VAT-2026-Q1" in result.out_path.read_text(encoding="utf-8")


# ── payment allocation by amount (Art. 86/87 OR) ──────────────────────────────

_RECEIVABLE = "Assets:CH:GmbH:Receivable:Trade"

# ACME202602 was short-paid by 0.47; ACME202603 is fully open.
_SHORT_LEDGER = """
2024-01-01 open Assets:CH:GmbH:Current:UBS:CHF CHF
2024-01-01 open Assets:CH:GmbH:Receivable:Trade
2024-01-01 open Income:CH:GmbH:Consulting:External:Domestic

2026-03-02 * "ACME" "February invoiced" ^ACME202602
    invoice: "ACME202602"
    Assets:CH:GmbH:Receivable:Trade            5691.47 CHF
    Income:CH:GmbH:Consulting:External:Domestic

2026-04-01 * "ACME" "March invoiced" ^ACME202603
    invoice: "ACME202603"
    Assets:CH:GmbH:Receivable:Trade            4075.37 CHF
    Income:CH:GmbH:Consulting:External:Domestic

2026-04-08 * "ACME" "February received" ^ACME202602
    invoice: "ACME202602"
    ubs_ref: "0630098TO1457669"
    Assets:CH:GmbH:Current:UBS:CHF             5691.00 CHF
    Assets:CH:GmbH:Receivable:Trade
"""

# Two invoices open, nothing quoted on the instalment.
_INSTALMENT_LEDGER = """
2024-01-01 open Assets:CH:GmbH:Current:UBS:CHF CHF
2024-01-01 open Assets:CH:GmbH:Receivable:Trade
2024-01-01 open Income:CH:GmbH:Consulting:External:Domestic

2026-07-02 * "ACME" "June invoiced" ^ACME202606
    invoice: "ACME202606"
    Assets:CH:GmbH:Receivable:Trade            5059.10 CHF
    Income:CH:GmbH:Consulting:External:Domestic

2026-08-03 * "ACME" "July invoiced" ^ACME202607
    invoice: "ACME202607"
    Assets:CH:GmbH:Receivable:Trade            2951.15 CHF
    Income:CH:GmbH:Consulting:External:Domestic
"""


def _payment(payee: str, narration: str, amount: str) -> data.Transaction:
    """An incoming draft whose payee rule already routed it to the receivable."""
    d = _draft(payee, narration, amount)
    return d._replace(postings=[d.postings[0], d.postings[1]._replace(account=_RECEIVABLE)])


def _match(ledger_text: str, *drafts: data.Transaction) -> importing.ImportResult:
    from beancount.loader import load_string

    entries, errors, _ = load_string(ledger_text)
    assert not errors, errors
    result = importing.ImportResult(source="test", drafts=list(drafts))
    importing.match_receivables(result, entries, config.Config())
    return result


def _open_after(ledger_text: str, *drafts: data.Transaction) -> dict[str, Decimal]:
    """Book the drafts as printed and return what is still open per invoice."""
    from beancount.loader import load_string
    from beancount.parser import printer

    from quints import receivables

    booked = ledger_text + "".join("\n" + printer.format_entry(d) for d in drafts)
    entries, errors, _ = load_string(booked)
    assert not errors, errors
    return {
        o.number: o.open_amount
        for o in receivables.compute_from_entries(entries, date(2026, 12, 31), config.Config())
    }


def _legs(t: data.Transaction) -> list[tuple[Decimal | None, str | None]]:
    """Each receivable leg as (amount, posting-level invoice:)."""
    return [
        (p.units.number if p.units else None, (p.meta or {}).get("invoice"))
        for p in t.postings
        if p.account == _RECEIVABLE
    ]


def test_exact_referenced_payment_stays_complete() -> None:
    result = _match(_SHORT_LEDGER, _payment("ACME AG", "Rechnung ACME202603", "4075.37"))
    d = result.drafts[0]
    assert d.flag == "*"
    assert d.meta["invoice"] == "ACME202603" and d.links == {"ACME202603"}
    assert _legs(d) == [(None, None)]
    assert "__note__" not in d.meta
    assert "ACME202603" not in _open_after(_SHORT_LEDGER, d)


def test_overpayment_explained_by_an_older_short_payment_is_split() -> None:
    """Failure 1: the 0.47 short on ACME202602 came with the ACME202603 payment."""
    result = _match(_SHORT_LEDGER, _payment("ACME AG", "Rechnung ACME202603", "4075.84"))
    d = result.drafts[0]
    assert d.flag == "!"
    assert "invoice" not in d.meta
    assert _legs(d) == [
        (Decimal("-4075.37"), "ACME202603"),
        (Decimal("-0.47"), "ACME202602"),
    ]
    assert d.meta["__note__"] == (
        "overpaid ACME202603 by 0.47 CHF — applied to older open ACME202602 0.47"
    )
    assert _open_after(_SHORT_LEDGER, d) == {}


def test_short_payment_stays_on_its_invoice_and_says_so() -> None:
    result = _match(_SHORT_LEDGER, _payment("ACME AG", "Rechnung ACME202603", "4075.00"))
    d = result.drafts[0]
    assert d.flag == "!"
    assert d.meta["invoice"] == "ACME202603" and _legs(d) == [(None, None)]
    assert d.meta["__note__"] == "short-paid ACME202603 by 0.37 CHF"
    assert _open_after(_SHORT_LEDGER, d)["ACME202603"] == Decimal("0.37")


def test_unexplained_overpayment_stays_on_its_invoice_and_says_so() -> None:
    result = _match(_SHORT_LEDGER, _payment("ACME AG", "Rechnung ACME202603", "4100.00"))
    d = result.drafts[0]
    assert d.flag == "!"
    assert d.meta["invoice"] == "ACME202603" and _legs(d) == [(None, None)]
    assert d.meta["__note__"] == ("overpaid ACME202603 by 24.63 CHF — no open invoice explains it")
    assert _open_after(_SHORT_LEDGER, d)["ACME202603"] == Decimal("-24.63")


def test_unreferenced_instalment_goes_to_the_oldest_invoice() -> None:
    """Failure 2: 1000.00 with no invoice number, two invoices open."""
    result = _match(_INSTALMENT_LEDGER, _payment("ACME AG", "Zahlung", "1000.00"))
    d = result.drafts[0]
    assert d.flag == "!"
    assert d.meta["invoice"] == "ACME202606" and d.links == {"ACME202606"}
    assert _legs(d) == [(None, None)]
    assert d.meta["__note__"] == (
        "no invoice reference — applied to the debt due first (Art. 87 Abs. 1 OR): "
        "ACME202606 1000.00 CHF; no due: recorded, ordered by invoice date"
    )
    assert _open_after(_INSTALMENT_LEDGER, d) == {
        "ACME202606": Decimal("4059.10"),
        "ACME202607": Decimal("2951.15"),
    }


def test_unreferenced_payment_beyond_every_invoice_leaves_the_rest_untagged() -> None:
    from beancount.loader import load_string
    from beancount.parser import printer

    from quints import receivables

    result = _match(_INSTALMENT_LEDGER, _payment("ACME AG", "Zahlung", "8100.00"))
    d = result.drafts[0]
    assert d.flag == "!"
    assert not d.links and "invoice" not in d.meta  # neither may claim the remainder
    assert _legs(d) == [
        (Decimal("-5059.10"), "ACME202606"),
        (Decimal("-2951.15"), "ACME202607"),
        (Decimal("-89.75"), None),
    ]
    assert d.meta["__note__"].endswith("; 89.75 CHF left without an invoice")
    assert _open_after(_INSTALMENT_LEDGER, d) == {}
    entries, _, _ = load_string(_INSTALMENT_LEDGER + "\n" + printer.format_entry(d))
    (untracked,) = receivables.reconcile(entries, date(2026, 12, 31), config.Config())
    assert untracked.posted == Decimal("-89.75")


def test_unreferenced_payment_follows_a_recorded_due_date() -> None:
    ledger_text = _INSTALMENT_LEDGER.replace(
        '    invoice: "ACME202607"\n', '    invoice: "ACME202607"\n    due: 2026-08-31\n'
    ).replace('    invoice: "ACME202606"\n', '    invoice: "ACME202606"\n    due: 2026-09-30\n')
    d = _match(ledger_text, _payment("ACME AG", "Zahlung", "1000.00")).drafts[0]
    assert d.meta["invoice"] == "ACME202607"
    assert "ordered by invoice date" not in d.meta["__note__"]


def test_unreferenced_payment_shares_same_day_debts_pro_rata() -> None:
    ledger_text = _INSTALMENT_LEDGER.replace("2026-08-03", "2026-07-02").replace(
        "2951.15", "1000.00"
    )
    d = _match(ledger_text, _payment("ACME AG", "Zahlung", "100.01")).drafts[0]
    legs = _legs(d)
    assert legs == [(Decimal("-83.50"), "ACME202606"), (Decimal("-16.51"), "ACME202607")]
    assert sum(a for a, _ in legs if a is not None) == Decimal("-100.01")
    assert "shared pro rata (Art. 87 Abs. 2 OR)" in d.meta["__note__"]


def test_unreferenced_payment_from_an_ambiguous_payee_is_left_alone() -> None:
    ledger_text = _INSTALMENT_LEDGER.replace('"ACME" "July invoiced"', '"ACME Services" "July"')
    draft = _payment("ACME", "Zahlung", "1000.00")
    result = _match(ledger_text, draft, _draft("ACME AG", "Zahlung", "50.00"))
    assert result.drafts[0] == draft  # "ACME" fits both payees
    assert result.drafts[1].postings[1].account == "Expenses:CH:GmbH:FIXME"  # a rule's leg wins
    assert result.receivable_matches == []


def _cash(t: data.Transaction) -> Decimal | None:
    units = t.postings[0].units
    return units.number if units else None


def test_import_allocates_once_writes_the_note_and_stays_idempotent(tmp_path: Path) -> None:
    """The fixture's 4075.84 credit is the real failure: no reference, and
    0.47 more than ACME202603. It settles both invoices — and the 2107.00
    credit after it finds nothing left to settle."""
    from beancount.parser import printer

    from quints import match

    ledger_file = tmp_path / "main.bean"
    ledger_file.write_text(_SHORT_LEDGER)
    result = importing.run_ubs(FIXTURE, ledger_file, out_dir=tmp_path / "staging", cfg=_CFG)

    by_amount = {_cash(d): d for d in result.drafts}
    split = by_amount[Decimal("4075.84")]
    assert split.flag == "!"
    assert _legs(split) == [(Decimal("-0.47"), "ACME202602"), (Decimal("-4075.37"), "ACME202603")]
    later = by_amount[Decimal("2107.00")]
    assert later.flag == "*" and "invoice" not in later.meta  # untouched rule draft
    assert result.out_path is not None
    assert "; no invoice reference — applied to the debt due first" in result.out_path.read_text()
    assert match.load_staging(tmp_path / "staging")  # the comment keeps the file parseable

    booked = printer.format_entry(split)
    ledger_file.write_text(_SHORT_LEDGER + "\n" + booked)
    again = importing.run_ubs(FIXTURE, ledger_file, out_dir=tmp_path / "again", cfg=_CFG)
    assert Decimal("4075.84") not in {_cash(d) for d in again.drafts}

    # Booked before the importer stamped refs: the legacy amount match catches it.
    ledger_file.write_text(
        _SHORT_LEDGER.replace('    ubs_ref: "0630098TO1457669"\n', "")
        + "\n"
        + booked.replace('  ubs_ref: "0130120TP1382853"\n', "")
    )
    legacy = importing.run_ubs(FIXTURE, ledger_file, out_dir=tmp_path / "legacy", cfg=_CFG)
    assert Decimal("4075.84") not in {_cash(d) for d in legacy.drafts}
    assert Decimal("4075.84") in {_cash(t) for t, _ in legacy.legacy_matches}


def test_referenced_bill_paid_short_is_flagged() -> None:
    from beancount.loader import load_string

    entries, _, _ = load_string(
        """
2024-01-01 open Liabilities:CH:GmbH:Payable:Trade
2024-01-01 open Expenses:CH:GmbH:Marketing:Tools
2026-07-01 * "Supplier" "Tools" ^SUP2026001
    bill: "SUP2026001"
    Liabilities:CH:GmbH:Payable:Trade   -500.00 CHF
    Expenses:CH:GmbH:Marketing:Tools
"""
    )
    result = importing.ImportResult(
        source="test", drafts=[_draft("Supplier", "SUP2026001", "-480.00")]
    )
    importing.match_payables(result, entries, config.Config())
    d = result.drafts[0]
    assert d.flag == "!" and d.meta["bill"] == "SUP2026001"
    assert d.meta["__note__"] == "short-paid SUP2026001 by 20.00 CHF"
