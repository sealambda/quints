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


def test_vat_convert_json(tmp_path: Path) -> None:
    led = tmp_path / "m.bean"
    led.write_text(LEDGER)
    res = runner.invoke(
        app,
        ["vat", "convert", "100", "EUR", "2026-07-02", "--bezugsteuer", "--json", "-f", str(led)],
    )
    assert res.exit_code == 0, res.output
    d = json.loads(res.output)
    assert d["bezugsteuer"] is True and d["currency"] == "EUR"
    assert d["chf"] == "7.53"  # 8.1% of 100 EUR at 0.93, to the Rappen
    assert "Bezugsteuer" in d["posting_text"]


def test_vat_report_and_settle_json(tmp_path: Path) -> None:
    led = tmp_path / "m.bean"
    led.write_text(LEDGER)
    res = runner.invoke(app, ["vat", "report", "-q", "2026-Q3", "--json", "-f", str(led)])
    assert res.exit_code == 0, res.output
    d = json.loads(res.output)
    assert d["date_from"] == "2026-07-01" and d["date_to"] == "2026-09-30"

    res = runner.invoke(app, ["vat", "settle", "-q", "2026-Q3", "--json", "-f", str(led)])
    assert res.exit_code == 0, res.output
    d = json.loads(res.output)
    assert d["report"]["date_from"] == "2026-07-01"
    assert d["settlement"]["link"] == "VAT-2026-Q3"
    assert "VAT Settlement" in d["settlement"]["text"]


FORM_310_LEDGER = """
2026-01-01 open Assets:CH:GmbH:Current:UBS:CHF CHF
  kmu: "1020"
2026-01-01 open Assets:CH:GmbH:Tax:InputVAT CHF
  kmu: "1170"
2026-01-01 open Assets:CH:GmbH:Equipment:Office CHF
  kmu: "1510"
2026-01-01 open Liabilities:CH:GmbH:Tax:OutputVAT CHF
  kmu: "2200"
2026-01-01 open Income:CH:GmbH:Consulting:External:Domestic CHF
  kmu: "3400"
2026-01-01 open Income:CH:GmbH:Books:Reduced CHF
  kmu: "3200"
2026-01-01 open Income:CH:GmbH:Consulting:External:Export CHF
  kmu: "3400"

2026-07-02 * "Acme AG" "Consulting"
  Assets:CH:GmbH:Current:UBS:CHF                 1081.00 CHF
  Income:CH:GmbH:Consulting:External:Domestic   -1000.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT               -81.00 CHF

2026-07-05 * "Buchhandlung" "Books"
  Assets:CH:GmbH:Current:UBS:CHF                  513.00 CHF
  Income:CH:GmbH:Books:Reduced                   -500.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT               -13.00 CHF

2026-07-10 * "Globex Ltd" "Export consulting"
  Assets:CH:GmbH:Current:UBS:CHF                  300.00 CHF
  Income:CH:GmbH:Consulting:External:Export      -300.00 CHF

2026-08-20 * "Möbel AG" "Desk"
  Assets:CH:GmbH:Equipment:Office                1000.00 CHF
  Assets:CH:GmbH:Tax:InputVAT                      81.00 CHF
  Assets:CH:GmbH:Current:UBS:CHF                -1081.00 CHF

2026-09-01 * "Acme AG" "VAT at the wrong rate"
  Assets:CH:GmbH:Current:UBS:CHF                  107.70 CHF
  Income:CH:GmbH:Consulting:External:Domestic    -100.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT                -7.70 CHF
"""


def test_vat_report_json_covers_form_310(tmp_path: Path) -> None:
    """The whole return through the CLI: every Ziffer, as decimal strings."""
    led = tmp_path / "m.bean"
    led.write_text(FORM_310_LEDGER)
    res = runner.invoke(app, ["vat", "report", "-q", "2026-Q3", "--json", "-f", str(led)])
    assert res.exit_code == 0, res.output
    d = json.loads(res.output)
    assert d["z200"] == "1900.00" and d["z221"] == "300.00"
    assert d["z289"] == "300.00" and d["z299"] == "1600.00"
    assert (d["z303_net"], d["z303_tax"]) == ("1100.00", "88.70")
    assert (d["z313_net"], d["z313_tax"]) == ("500.00", "13.00")
    assert d["z399"] == "101.70"
    # The desk is an investment (kmu 1510) → Ziffer 405, not 400.
    assert (d["z400"], d["z405"], d["z479"]) == ("0", "81.00", "81.00")
    assert d["z500"] == "20.70" and d["z510"] == "0"
    rows = {r["ziffer"]: r for r in d["rate_rows"]}
    assert rows["303"]["rate"] == "0.081" and rows["313"]["rate"] == "0.026"
    assert rows["343"]["rate"] == "0.038" and rows["343"]["net"] == "0"
    assert "302" not in rows  # the old vintage is unused and stays hidden
    # 7.70 on 100.00 is last year's rate — reported, not silently filed.
    (v,) = d["violations"]
    assert v["expected"] == "8.10" and v["posted"] == "7.70"


def test_vat_convert_json_rate_class(tmp_path: Path) -> None:
    led = tmp_path / "m.bean"
    led.write_text(LEDGER)
    argv = ["vat", "convert", "100", "EUR", "2026-07-02", "--net", "--rate", "reduced"]
    res = runner.invoke(app, [*argv, "--json", "-f", str(led)])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["chf"] == "2.42"  # 2.6 % of 100 EUR at 0.93

    bad = runner.invoke(app, [*argv[:-1], "nope", "-f", str(led)])
    assert bad.exit_code == 1 and "unknown VAT rate class" in bad.output


def test_vat_status_json(tmp_path: Path) -> None:
    led = tmp_path / "m.bean"
    led.write_text(LEDGER)
    res = runner.invoke(app, ["vat", "status", "--json", "-f", str(led)])
    assert res.exit_code == 0, res.output
    d = json.loads(res.output)
    assert d["liabilities"] == [] and d["total_owed"] == "0"


def test_check_json_ok_and_errors(tmp_path: Path) -> None:
    led = tmp_path / "m.bean"
    led.write_text(LEDGER)
    res = runner.invoke(app, ["check", "--json", "-f", str(led)])
    assert res.exit_code == 0
    d = json.loads(res.output)
    assert d["ok"] is True and d["errors"] == []
    assert d["stats"]["directives"] == 2 and d["stats"]["by_type"] == {"Open": 1, "Price": 1}

    led.write_text(LEDGER + "\n2026-07-02 balance Assets:CH:GmbH:Tax:InputVAT 9.99 CHF\n")
    res = runner.invoke(app, ["check", "--json", "-f", str(led)])
    d = json.loads(res.output)
    assert res.exit_code == 1 and d["ok"] is False and d["errors"][0]["line"]


def test_check_verbose_summary(tmp_path: Path) -> None:
    led = tmp_path / "m.bean"
    led.write_text(LEDGER)
    res = runner.invoke(app, ["check", "-v", "-f", str(led)])
    assert res.exit_code == 0, res.output
    assert "OK — no errors." in res.output
    assert "Open" in res.output and "Price" in res.output  # the breakdown table


RECEIVABLES_LEDGER = """
2024-01-01 open Assets:CH:GmbH:Receivable:Trade
2024-01-01 open Income:CH:GmbH:Consulting:External:Domestic
2024-01-01 open Income:CH:GmbH:Consulting:External:Export
2026-07-02 * "ACME" "June" ^ACME202606
  invoice: "ACME202606"
  Assets:CH:GmbH:Receivable:Trade   100.00 CHF
  Income:CH:GmbH:Consulting:External:Domestic
2026-07-03 * "Globex" "June" ^GLOBEX202606
  invoice: "GLOBEX202606"
  Assets:CH:GmbH:Receivable:Trade   200.00 EUR
  Income:CH:GmbH:Consulting:External:Export
2026-07-10 price EUR 0.93 CHF
"""


def test_receivables_json(tmp_path: Path) -> None:
    led = tmp_path / "m.bean"
    led.write_text(RECEIVABLES_LEDGER)
    res = runner.invoke(app, ["receivables", "--at", "2026-07-12", "--json", "-f", str(led)])
    assert res.exit_code == 0, res.output
    d = json.loads(res.output)
    assert d["open"][0]["number"] == "ACME202606"
    assert d["open"][0]["open_amount"] == "100.00" and d["open"][0]["age_days"] == 10
    totals = {t["currency"]: t for t in d["totals"]}
    assert totals["CHF"]["converted"] == "100.00"
    assert totals["EUR"]["converted"] == "186.00"  # 200 EUR at 0.93
    assert d["consolidated"] == {"currency": "CHF", "total": "286.00", "missing_rates": []}


def test_receivables_json_consolidation_currency_and_missing_rate(tmp_path: Path) -> None:
    led = tmp_path / "m.bean"
    led.write_text(RECEIVABLES_LEDGER)
    # --in EUR: the EUR total needs no rate; CHF converts through the inverse.
    res = runner.invoke(
        app, ["receivables", "--at", "2026-07-12", "--in", "EUR", "--json", "-f", str(led)]
    )
    assert res.exit_code == 0, res.output
    d = json.loads(res.output)
    assert d["consolidated"]["currency"] == "EUR" and d["consolidated"]["missing_rates"] == []

    # A currency with no rate is excluded and reported.
    res = runner.invoke(
        app, ["receivables", "--at", "2026-07-12", "--in", "USD", "--json", "-f", str(led)]
    )
    assert res.exit_code == 0, res.output
    d = json.loads(res.output)
    assert sorted(d["consolidated"]["missing_rates"]) == ["CHF", "EUR"]
    assert d["consolidated"]["total"] == "0"


PAYABLES_LEDGER = """
2024-01-01 open Liabilities:CH:GmbH:Payable:Trade
2024-01-01 open Assets:CH:GmbH:Current:UBS:CHF
2024-01-01 open Expenses:CH:GmbH:Admin:Bookkeeping

2026-07-02 * "Treuhand Muster" "Bookkeeping Q2" ^TM-2026-4711
  bill: "TM-2026-4711"
  due: 2026-08-01
  Expenses:CH:GmbH:Admin:Bookkeeping   480.00 CHF
  Liabilities:CH:GmbH:Payable:Trade

2026-07-03 * "Foreign SaaS" "Hosting" ^FS-9001
  bill: "FS-9001"
  Expenses:CH:GmbH:Admin:Bookkeeping   200.00 EUR
  Liabilities:CH:GmbH:Payable:Trade

2026-07-10 price EUR 0.93 CHF
"""


def test_payables_json(tmp_path: Path) -> None:
    led = tmp_path / "m.bean"
    led.write_text(PAYABLES_LEDGER)
    res = runner.invoke(app, ["payables", "--at", "2026-08-15", "--json", "-f", str(led)])
    assert res.exit_code == 0, res.output
    d = json.loads(res.output)
    first, second = d["open"]
    assert first["number"] == "TM-2026-4711" and first["keyed_by"] == "bill"
    assert first["open_amount"] == "480.00" and first["days_overdue"] == 14
    assert first["due_date"] == "2026-08-01"
    # no `due:` on the EUR bill — billed 07-03 plus the default 30-day terms
    assert second["number"] == "FS-9001" and second["due_date"] == "2026-08-02"
    totals = {t["currency"]: t for t in d["totals"]}
    assert totals["EUR"]["converted"] == "186.00"  # 200 EUR at 0.93
    assert d["consolidated"] == {"currency": "CHF", "total": "666.00", "missing_rates": []}


def test_payables_and_match_end_to_end_through_the_cli(tmp_path: Path) -> None:
    # The two commands a review session actually runs, against one project.
    led = tmp_path / "main.bean"
    led.write_text(PAYABLES_LEDGER)
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "2026-07-21-ubs.bean").write_text(
        '2026-07-20 ! "TREUHAND MUSTER AG" "payment order"\n'
        "  Assets:CH:GmbH:Current:UBS:CHF  -480.00 CHF\n"
        "  Expenses:CH:GmbH:FIXME           480.00 CHF\n"
    )
    res = runner.invoke(app, ["payables", "--at", "2026-07-21", "-f", str(led)])
    assert res.exit_code == 0, res.output
    assert "TM-2026-4711" in res.output and "Open payables" in res.output

    res = runner.invoke(app, ["match", "--json", "-f", str(led), "--staging", str(staging)])
    assert res.exit_code == 0, res.output
    (m,) = [m for m in json.loads(res.output)["matches"] if m["kind"] == "payment\u2192payable"]
    assert m["score"] == 1.0 and m["target"]["bill"] == "TM-2026-4711"
    assert "amount equals open 480.00 CHF" in m["reasons"]


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


def test_invoice_json_reports_the_reference_it_used(tmp_path: Path) -> None:
    issuer = tmp_path / "issuer.yaml"
    issuer.write_text(
        "name: Muster GmbH\n"
        "address: [Musterstrasse 1, 3000 Bern]\n"
        "vat_id: CHE-267.359.056 MWST\n"
        "bank:\n"
        "  CHF:\n"
        "    reference: scor\n"
        "    iban: CH93 0076 2011 6238 5295 7\n"
        "    qr_iban: CH44 3199 9123 0008 8901 2\n"
    )
    inv = tmp_path / "inv.yaml"
    inv.write_text(
        "number: INV2026014\nkind: domestic\ncurrency: CHF\nissue_date: 2026-07-02\n"
        "supply: 2026-06\n"
        "customer: {name: Acme AG, address: [Bahnhofstrasse 1, 8001 Zürich]}\n"
        "customer_reference: PO-2026-118\n"
        "items:\n  - {description: Consulting, quantity: 1, unit_price: 1000.00}\n"
    )
    res = runner.invoke(
        app,
        [
            "invoice",
            str(inv),
            "--issuer",
            str(issuer),
            "--out",
            str(tmp_path / "out.pdf"),
            "--no-verify",
            "--json",
        ],
    )
    assert res.exit_code == 0, res.output
    d = json.loads(res.output)
    # Both IBANs configured and `reference: scor` chosen → the creditor reference.
    assert d["reference_type"] == "SCOR" and d["reference"] == "RF47INV2026014"
    assert d["customer_reference"] == "PO-2026-118"
    assert d["qr_payload_ok"] is True


def test_iban_json_checks_a_pair(tmp_path: Path) -> None:
    args = ["iban", "DE89 3704 0044 0532 0130 00", "--bic", "COBADEFFXXX", "--json"]
    res = runner.invoke(app, args)
    assert res.exit_code == 0, res.output
    d = json.loads(res.output)
    assert d["ok"] is True
    (c,) = d["checks"]
    assert c["formatted"] == "DE89 3704 0044 0532 0130 00" and c["bic"] == "COBADEFFXXX"
    assert c["country"] == "DE" and c["iid"] is None  # IID is a CH/LI thing


def test_iban_json_flags_a_missing_bic(tmp_path: Path) -> None:
    res = runner.invoke(app, ["iban", "CH44 3199 9123 0008 8901 2", "--json"])
    assert res.exit_code == 1, res.output  # non-zero: fits a pre-flight check
    (c,) = json.loads(res.output)["checks"]
    assert c["bic"] is None and c["iid"] == "31999"
    assert "no BIC" in c["problems"][0]


def test_iban_audits_the_issuer_config(tmp_path: Path) -> None:
    issuer = tmp_path / "issuer.yaml"
    issuer.write_text(
        "name: Muster GmbH\n"
        "address: [Musterstrasse 1, 3000 Bern]\n"
        "vat_id: CHE-267.359.056 MWST\n"
        "bank:\n"
        "  EUR:\n"
        "    iban: DE89 3704 0044 0532 0130 00\n"  # no bic — the failure being guarded
    )
    res = runner.invoke(app, ["iban", "--issuer", str(issuer), "--json"])
    assert res.exit_code == 1, res.output
    d = json.loads(res.output)
    assert d["ok"] is False
    assert [c["label"] for c in d["checks"]] == ["EUR"]
