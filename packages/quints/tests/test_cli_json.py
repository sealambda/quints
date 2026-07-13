"""JSON-output contract for the CLI (plan 5.2: machine-readable everywhere)."""

import json

from typer.testing import CliRunner

from quints.cli import app

runner = CliRunner()

LEDGER = """
2024-01-01 open Assets:CH:GmbH:Tax:InputVAT
2026-07-01 price EUR 0.93 CHF
"""


def test_vat_json(tmp_path):
    led = tmp_path / "m.bean"
    led.write_text(LEDGER)
    res = runner.invoke(
        app, ["vat", "100", "EUR", "2026-07-02", "--bezugsteuer", "--json", "-f", str(led)]
    )
    assert res.exit_code == 0, res.output
    d = json.loads(res.output)
    assert d["bezugsteuer"] is True and d["currency"] == "EUR"
    assert d["chf"] == "7.53"  # 8.1% of 100 EUR at 0.93, to the Rappen
    assert "Bezugsteuer" in d["posting_text"]


def test_check_json_ok_and_errors(tmp_path):
    led = tmp_path / "m.bean"
    led.write_text(LEDGER)
    res = runner.invoke(app, ["check", "--json", "-f", str(led)])
    assert res.exit_code == 0 and json.loads(res.output) == {"ok": True, "errors": []}

    led.write_text(LEDGER + "\n2026-07-02 balance Assets:CH:GmbH:Tax:InputVAT 9.99 CHF\n")
    res = runner.invoke(app, ["check", "--json", "-f", str(led)])
    d = json.loads(res.output)
    assert res.exit_code == 1 and d["ok"] is False and d["errors"][0]["line"]


def test_receivables_json(tmp_path):
    led = tmp_path / "m.bean"
    led.write_text("""
2024-01-01 open Assets:CH:GmbH:Receivable:Trade
2024-01-01 open Income:CH:GmbH:Consulting:External:Domestic
2026-07-02 * "ACME" "June" ^ACME202606
  invoice: "ACME202606"
  Assets:CH:GmbH:Receivable:Trade   100.00 CHF
  Income:CH:GmbH:Consulting:External:Domestic
""")
    res = runner.invoke(app, ["receivables", "--at", "2026-07-12", "--json", "-f", str(led)])
    assert res.exit_code == 0, res.output
    d = json.loads(res.output)
    assert d["open"][0]["number"] == "ACME202606"
    assert d["open"][0]["open_amount"] == "100.00" and d["open"][0]["age_days"] == 10
