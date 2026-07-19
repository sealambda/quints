"""JSON-output contract for the CLI (plan 5.2: machine-readable everywhere)."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quints.cli import app

runner = CliRunner()

LEDGER = """
2024-01-01 open Assets:CH:GmbH:Tax:InputVAT
2026-07-01 price EUR 0.93 CHF
"""


def test_vat_json(tmp_path: Path) -> None:
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


def test_check_json_ok_and_errors(tmp_path: Path) -> None:
    led = tmp_path / "m.bean"
    led.write_text(LEDGER)
    res = runner.invoke(app, ["check", "--json", "-f", str(led)])
    assert res.exit_code == 0 and json.loads(res.output) == {"ok": True, "errors": []}

    led.write_text(LEDGER + "\n2026-07-02 balance Assets:CH:GmbH:Tax:InputVAT 9.99 CHF\n")
    res = runner.invoke(app, ["check", "--json", "-f", str(led)])
    d = json.loads(res.output)
    assert res.exit_code == 1 and d["ok"] is False and d["errors"][0]["line"]


def test_receivables_json(tmp_path: Path) -> None:
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


def test_prices_sync_json_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real CLI driving the real BAZG source — only HTTP is faked.

    The file is pre-seeded as verified through five days ago, so the sync
    fetches exactly the five-day tail per currency.
    """
    from datetime import datetime, timedelta, timezone

    from beanprice_bazg import bazg

    def fake_http(url: str, params: dict[str, str]) -> str:
        d = params["d"]  # YYYYMMDD — echo the requested day back as <datum>
        return (
            f"<wechselkurse><datum>{d[6:8]}.{d[4:6]}.{d[0:4]}</datum>"
            '<devise code="usd"><waehrung>1 USD</waehrung><kurs>0.80123</kurs></devise>'
            '<devise code="eur"><waehrung>1 EUR</waehrung><kurs>0.93456</kurs></devise>'
            "</wechselkurse>"
        )

    monkeypatch.setattr(bazg, "_http_get", fake_http)
    today = datetime.now(timezone.utc).date()
    seeded = today - timedelta(days=5)
    out = tmp_path / "prices.bean"
    out.write_text(
        "; header\n"
        f"; quints: verified USD/CHF 2024-01-01..{seeded}\n"
        f"; quints: verified EUR/CHF 2024-01-01..{seeded}\n"
        f"\n{seeded} price USD 0.80123 CHF\n\n{seeded} price EUR 0.93456 CHF\n"
    )
    res = runner.invoke(app, ["prices", "sync", "--json", "--out", str(out)])
    assert res.exit_code == 0, res.output
    d = json.loads(res.output)
    assert d["wrote"] is True and d["added"] == 10  # 5 days x 2 currencies
    for ccy in ("USD", "EUR"):
        assert d["per_currency"][ccy] == {
            "added": 5,
            "healed": 0,
            "unavailable": 0,
            "had_through": str(seeded),
        }
    text = out.read_text()
    assert f"{today} price USD 0.80123 CHF" in text
    assert f"; quints: verified EUR/CHF 2024-01-01..{today}" in text


def test_prices_sync_reads_ledger_metadata_like_bean_price(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With commodity `price:` metadata in the ledger, sync fetches exactly the
    declared jobs (here: EUR only — not the USD/EUR config default)."""
    from datetime import datetime, timedelta, timezone

    from beanprice_bazg import bazg

    def fake_http(url: str, params: dict[str, str]) -> str:
        d = params["d"]
        return (
            f"<wechselkurse><datum>{d[6:8]}.{d[4:6]}.{d[0:4]}</datum>"
            '<devise code="eur"><waehrung>1 EUR</waehrung><kurs>0.93456</kurs></devise>'
            "</wechselkurse>"
        )

    monkeypatch.setattr(bazg, "_http_get", fake_http)
    (tmp_path / "main.bean").write_text(
        '2024-01-01 commodity EUR\n  price: "CHF:beanprice_bazg/EUR"\n'
    )
    (tmp_path / "quints.toml").write_text(f'[ledger]\nmain = "{tmp_path}/main.bean"\n')
    today = datetime.now(timezone.utc).date()
    seeded = today - timedelta(days=5)
    out = tmp_path / "prices.bean"
    out.write_text(f"; quints: verified EUR/CHF 2024-01-01..{seeded}\n")
    res = runner.invoke(
        app,
        ["--config", str(tmp_path / "quints.toml"), "prices", "sync", "--json", "--out", str(out)],
    )
    assert res.exit_code == 0, res.output
    d = json.loads(res.output)
    assert list(d["per_currency"]) == ["EUR"] and d["added"] == 5
    assert f"{today} price EUR 0.93456 CHF" in out.read_text()
