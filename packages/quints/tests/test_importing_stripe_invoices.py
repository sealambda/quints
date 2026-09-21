"""`quints import stripe --invoices`: customer invoice PDFs filed into inbox/.

No network: the Stripe client is stubbed and the PDF is a few synthetic bytes.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quints import config, importing
from quints.cli import app

runner = CliRunner()

ACCOUNT = "acct_TEST123"
TRANSACTIONS: list[dict[str, object]] = [
    {
        "id": "txn_charge_may",
        "object": "balance_transaction",
        "type": "charge",
        "currency": "eur",
        "created": 1778803200,  # 2026-05-15
        "amount": 14900,
        "fee": 509,
        "net": 14391,
        "description": "Subscription creation",
        "source": {
            "id": "ch_100",
            "object": "charge",
            "billing_details": {"name": "ACME Labs GmbH"},
        },
    },
    {
        "id": "txn_fee_may",
        "object": "balance_transaction",
        "type": "stripe_fee",
        "currency": "eur",
        "created": 1780185600,  # 2026-05-31
        "amount": -104,
        "fee": 0,
        "net": -104,
        "description": "Billing fees",
        "fee_details": [{"type": "tax", "amount": -8, "currency": "eur"}],
    },
    {
        "id": "txn_payout_jun",
        "object": "balance_transaction",
        "type": "payout",
        "currency": "eur",
        "created": 1782777600,
        "amount": -20000,
        "fee": 0,
        "net": -20000,
        "source": "po_300",
    },
]
PAYOUT = TRANSACTIONS[2]
STAGED: dict[str, object] = {"account": {"id": ACCOUNT}, "data": TRANSACTIONS}

INVOICE: dict[str, object] = {
    "id": "in_00000000000001",
    "object": "invoice",
    "number": "ABCD1234-0002",
    "status": "paid",
    "created": 1778803210,
    "total": 14900,
    "amount_paid": 14900,
    "currency": "eur",
    "customer": "cus_00000000000001",
    "customer_name": "ACME Labs GmbH",
    "customer_email": "billing@acme.test",
    "invoice_pdf": "https://pay.stripe.test/invoice/acct_TEST/live_TEST1/pdf",
    "charge": None,
    "payment_intent": None,
    "subscription": None,
    "paid": None,
}

PDF = b"%PDF-1.7 stub invoice"
FILED_NAME = "2026-05-15.acme-labs-gmbh.ABCD1234-0002.pdf"

CFG = config.Config(
    import_stripe=config.StripeImport(
        accounts=(("EUR", "Assets:CH:GmbH:Current:Stripe:EUR"),),
        account_id=ACCOUNT,
    )
)

LEDGER = """
2024-01-01 open Assets:CH:GmbH:Current:Stripe:EUR EUR
2024-01-01 open Expenses:CH:GmbH:BankFees:Stripe
2024-01-01 open Assets:CH:GmbH:Tax:InputVAT
"""


@dataclass
class Stub:
    """Stands in for StripeClient, recording what it was asked for."""

    invoices_payload: list[dict[str, object]]
    key_account: str = ACCOUNT  # the account the stubbed key belongs to
    window: tuple[int | None, int | None] | None = None
    downloaded: list[str] = field(default_factory=list)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub = self

        class _Client:
            def __init__(self, api_key: str) -> None:
                self.api_key = api_key

            def account(self) -> dict[str, object]:
                return {"id": stub.key_account}

            def invoices(
                self, created_gte: int | None = None, created_lte: int | None = None
            ) -> list[dict[str, object]]:
                stub.window = (created_gte, created_lte)
                return stub.invoices_payload

            def document(self, url: str) -> bytes:
                stub.downloaded.append(url)
                return PDF

        monkeypatch.setenv("QUINTS_STRIPE_API_KEY", "rk_test_x")
        monkeypatch.setattr(importing, "StripeClient", _Client)


def _project(tmp_path: Path) -> tuple[Path, Path]:
    """A ledger and a staged Stripe statement, the way --fetch leaves them."""
    ledger = tmp_path / "main.bean"
    ledger.write_text(LEDGER)
    statement = tmp_path / "staging" / "stripe-2026-05-01-2026-06-30.json"
    statement.parent.mkdir()
    statement.write_text(json.dumps(STAGED))
    return ledger, statement


def test_files_the_invoice_pdf_into_inbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger, statement = _project(tmp_path)
    stub = Stub([INVOICE])
    stub.install(monkeypatch)

    docs = importing.fetch_stripe_invoices([statement], ledger, CFG)

    assert len(docs) == 1
    doc = docs[0]
    assert doc.name == FILED_NAME  # YYYY-MM-DD.payee.narrative.ext
    assert doc.path == tmp_path / "inbox" / FILED_NAME
    assert doc.path.read_bytes() == PDF
    assert (doc.number, doc.customer, doc.txn_id) == (
        "ABCD1234-0002",
        "ACME Labs GmbH",
        "txn_charge_may",
    )
    assert doc.matched_by == "amount+time"
    assert doc.skipped is False

    # The window is derived from the charges in the statement, padded by the
    # correlation tolerance — the payout and the fee debit don't widen it.
    assert stub.window == (1778803200 - 120, 1778803200 + 120)
    assert stub.downloaded == [str(INVOICE["invoice_pdf"])]


def test_already_filed_invoices_are_skipped_not_refetched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Idempotent re-runs: inbox/ and documents/ both count as already filed."""
    ledger, statement = _project(tmp_path)
    stub = Stub([INVOICE])
    stub.install(monkeypatch)

    # First run downloads it.
    first = importing.fetch_stripe_invoices([statement], ledger, CFG)
    assert [d.skipped for d in first] == [False]

    # Second run finds it in inbox/.
    second = importing.fetch_stripe_invoices([statement], ledger, CFG)
    assert [d.skipped for d in second] == [True]
    assert second[0].path == tmp_path / "inbox" / FILED_NAME
    assert stub.downloaded == [str(INVOICE["invoice_pdf"])]  # not downloaded twice

    # Once booked the document moves under documents/ — still not refetched.
    filed = tmp_path / "documents" / "Income" / "CH" / "GmbH" / "Export"
    filed.mkdir(parents=True)
    (tmp_path / "inbox" / FILED_NAME).rename(filed / FILED_NAME)

    third = importing.fetch_stripe_invoices([statement], ledger, CFG)
    assert [d.skipped for d in third] == [True]
    assert third[0].path == filed / FILED_NAME
    assert not (tmp_path / "inbox" / FILED_NAME).exists()
    assert stub.downloaded == [str(INVOICE["invoice_pdf"])]


def test_ambiguous_correlation_files_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two candidate invoices: refuse rather than file against the wrong entry."""
    ledger, statement = _project(tmp_path)
    twin = INVOICE | {"id": "in_00000000000009", "number": "ABCD1234-0009"}
    stub = Stub([INVOICE, twin])
    stub.install(monkeypatch)

    with pytest.raises(importing.StripeError) as excinfo:
        importing.fetch_stripe_invoices([statement], ledger, CFG)

    message = str(excinfo.value)
    assert "cannot tell which invoice documents which charge" in message
    assert "ABCD1234-0002, ABCD1234-0009" in message
    assert stub.downloaded == []
    assert not (tmp_path / "inbox").exists()


def test_no_charges_means_no_api_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = tmp_path / "main.bean"
    ledger.write_text(LEDGER)
    statement = tmp_path / "payouts-only.json"
    statement.write_text(json.dumps({"data": [PAYOUT]}))
    stub = Stub([INVOICE])
    stub.install(monkeypatch)

    assert importing.fetch_stripe_invoices([statement], ledger, CFG) == []
    assert stub.window is None


def test_a_key_for_another_account_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The statements were checked against the config; the key is separate.

    A rotated or second-entity key would list another account's invoices, and
    those correlate against these charges on amount and time just as well — so
    the wrong customer's invoice would land in inbox/ looking perfectly valid.
    """
    ledger, statement = _project(tmp_path)
    stub = Stub([INVOICE], key_account="acct_SOMEONE_ELSE")
    stub.install(monkeypatch)

    with pytest.raises(importing.StripeError, match="wrong Stripe account"):
        importing.fetch_stripe_invoices([statement], ledger, CFG)

    assert stub.window is None  # no invoices listed
    assert stub.downloaded == []
    assert not (tmp_path / "inbox").exists()


def test_run_stripe_flags_the_months_whose_fees_carry_vat(tmp_path: Path) -> None:
    """Stripe's own tax invoice has no API — the import can only say when."""
    ledger, statement = _project(tmp_path)

    result = importing.run_stripe([statement], ledger, tmp_path / "out", CFG)

    assert result.fee_tax_periods == ["2026-05"]


def test_cli_invoices_reports_every_file_and_the_fee_reminder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger, statement = _project(tmp_path)
    toml = tmp_path / "quints.toml"
    toml.write_text(
        "[import.stripe]\n"
        f'account_id = "{ACCOUNT}"\n'
        'fees_account = "Expenses:CH:GmbH:BankFees:Stripe"\n'
        'tax_account = "Assets:CH:GmbH:Tax:InputVAT"\n'
        "\n[import.stripe.accounts]\n"
        'EUR = "Assets:CH:GmbH:Current:Stripe:EUR"\n'
    )
    Stub([INVOICE]).install(monkeypatch)
    argv = [
        "--config",
        str(toml),
        "import",
        "stripe",
        str(statement),
        "--invoices",
        "--out",
        str(tmp_path / "staging"),
        "-f",
        str(ledger),
    ]

    res = runner.invoke(app, argv)
    assert res.exit_code == 0, res.output
    assert "1 customer invoice(s):" in res.output
    # Reported relative to the working directory when it is under it, so the
    # line stays readable; tmp_path is not, so the absolute path shows.
    assert f"invoice ABCD1234-0002 → {tmp_path / 'inbox' / FILED_NAME}" in res.output
    assert "txn_charge_may, amount+time" in res.output
    assert "Stripe charged VAT on its 2026-05 fees" in res.output
    assert "Settings → Plans and fees" in res.output

    # Re-run: the same document is reported as skipped, never silently.
    res = runner.invoke(app, argv)
    assert res.exit_code == 0, res.output
    assert "already filed as" in res.output and "skipped" in res.output

    res = runner.invoke(app, [*argv, "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["fee_tax_periods"] == ["2026-05"]
    assert [(i["number"], i["skipped"], i["matched_by"]) for i in payload["invoices"]] == [
        ("ABCD1234-0002", True, "amount+time")
    ]
