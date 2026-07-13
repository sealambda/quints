"""Integration test for `quints import ubs` (fixture statement → staging drafts)."""

from decimal import Decimal
from pathlib import Path

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


def test_run_ubs_dedups_and_stages(tmp_path):
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

    staged = result.out_path.read_text()
    assert 'ubs_ref: "9902063AR6387321"' in staged           # share-capital draft
    assert 'ubs_ref: "9930635BN7487612"' not in staged        # already imported
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


def test_run_wise_merges_conversions_and_dedups(tmp_path):
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
    assert conversion.postings[1].price.number == Decimal("49.76")  # 50.00 − 0.24 fee

    # One closing-balance assertion per currency file.
    assert len(result.balances) == 2
    staged = result.out_path.read_text()
    assert "2026-07-11 balance Assets:CH:GmbH:Current:Wise:EUR" in staged
    assert "2026-07-11 balance Assets:CH:GmbH:Current:Wise:USD" in staged


def test_rules_draft_counter_legs(tmp_path):
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


def _draft(payee, narration, amount, meta=None):
    from beancount.core import data
    from beancount.core.amount import Amount

    return data.Transaction(
        meta=dict(meta or {}), date=__import__("datetime").date(2026, 7, 20),
        flag="!", payee=payee, narration=narration,
        tags=frozenset(), links=frozenset(),
        postings=[
            data.Posting("Assets:CH:GmbH:Current:UBS:CHF",
                         Amount(Decimal(amount), "CHF"), None, None, None, None),
            data.Posting("Expenses:CH:GmbH:FIXME",
                         Amount(-Decimal(amount), "CHF"), None, None, None, None),
        ],
    )


def test_match_receivables_by_qrr_and_scor(tmp_path):
    from beancount.loader import load_string

    from quints import config
    from quints.invoice.model import make_qrr, make_scor

    entries, _, _ = load_string(_RECV_LEDGER)
    qrr = make_qrr("ACME202606")
    spaced_rf = " ".join([make_scor("ACME202606")[i:i + 4] for i in range(0, 30, 4)])

    result = importing.ImportResult(source="test")
    result.drafts = [
        _draft("ACME AG", "payment", "5059.10", {"ubs_ref": f"X1 {qrr}"}),
        _draft("ACME AG", f"ref {spaced_rf}", "1000.00"),
        _draft("ACME AG", f"refund {qrr}", "-50.00"),      # outgoing → untouched
        _draft("Somebody", "unrelated", "12.00"),
    ]
    importing._match_receivables(result, entries, config.Config())

    assert [n for n, _ in result.receivable_matches] == ["ACME202606", "ACME202606"]
    matched = result.drafts[0]
    assert matched.flag == "*"
    assert matched.postings[1].account == "Assets:CH:GmbH:Receivable:Trade"
    assert "ACME202606" in matched.links
    assert matched.meta["invoice"] == "ACME202606"
    assert result.drafts[2].flag == "!"                     # outgoing untouched
    assert result.drafts[3].postings[1].account == "Expenses:CH:GmbH:FIXME"
