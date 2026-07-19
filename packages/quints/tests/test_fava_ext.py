"""The Quint Fava extension renders the review panel (docs/plans/06)."""

from pathlib import Path

from fava.application import create_app

LEDGER = """
2024-01-01 custom "fava-extension" "quints.fava"
2024-01-01 open Assets:CH:GmbH:Receivable:Trade
2024-01-01 open Assets:CH:GmbH:Current:UBS:CHF
2024-01-01 open Income:CH:GmbH:Consulting:External:Domestic
2024-01-01 open Liabilities:CH:GmbH:Tax:PayableVAT

2026-07-02 * "ACME" "June invoiced" ^ACME202606
  invoice: "ACME202606"
  Assets:CH:GmbH:Receivable:Trade        500.00 CHF
  Income:CH:GmbH:Consulting:External:Domestic

2026-07-10 * "ESTV" "Q2 settled" ^VAT-2026-Q2
  due: 2026-08-31
  Liabilities:CH:GmbH:Tax:PayableVAT    -123.45 CHF
  Income:CH:GmbH:Consulting:External:Domestic
"""


def test_dashboard_renders(tmp_path: Path):
    led = tmp_path / "main.bean"
    led.write_text(LEDGER)
    (tmp_path / "staging").mkdir()
    (tmp_path / "staging" / "2026-07-12-ubs.bean").write_text(
        '2026-07-11 ! "Somebody" "draft"\n'
        "  Assets:CH:GmbH:Current:UBS:CHF  1.00 CHF\n"
        "  Expenses:CH:GmbH:FIXME\n"
    )
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "2026-07-12.receipt.pdf").write_bytes(b"%PDF-")

    app = create_app([str(led)], load=True)
    client = app.test_client()
    slug = client.get("/").headers["Location"].strip("/").split("/")[0]
    page = client.get(f"/{slug}/extension/QuintDashboard/")
    assert page.status_code == 200
    html = page.get_data(as_text=True)

    assert "ACME202606" in html and "500.00" in html  # receivable open
    assert "VAT-2026-Q2" in html and "123.45" in html  # VAT outstanding
    assert "2026-07-12-ubs.bean" in html  # staging queue
    assert "2026-07-12.receipt.pdf" in html  # inbox backlog
