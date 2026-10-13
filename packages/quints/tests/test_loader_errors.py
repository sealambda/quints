"""Loader errors are violations: every money report still renders and says so.

A ledger beancount cannot fully load (a mistyped include, a syntax error, a
failing balance assertion) silently loses entries. A VAT return or a balance
sheet computed from what is left is short, so each report lists the loader's
errors next to its figures instead of dropping them.
"""

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quints import config, importing, kmu, ledger, mwst, report_pdf
from quints.cli import app

runner = CliRunner()

FIXTURES = Path(__file__).parent / "fixtures"

# A mistyped include (silently drops a whole file) and a syntax error (drops
# the second sale): what is left loads and would file a short return.
BROKEN = """\
include "acounts.bean"
2026-01-01 open Assets:CH:GmbH:Current:UBS:CHF CHF
  kmu: "1020"
2026-01-01 open Liabilities:CH:GmbH:Tax:OutputVAT CHF
  kmu: "2200"
2026-01-01 open Income:CH:GmbH:Consulting:External:Domestic CHF
  kmu: "3400"

2026-07-02 * "Acme AG" "Consulting"
  Assets:CH:GmbH:Current:UBS:CHF                 1081.00 CHF
  Income:CH:GmbH:Consulting:External:Domestic   -1000.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT               -81.00 CHF

2026-07-03 * "Acme AG" "Consulting, again"
  Assets:CH:GmbH:Current:UBS:CHF                 1081.00 CHF
  Income:CH:GmbH:Consulting:External:Domestic   -1000.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT               -81.00 CH F
"""


def _broken(tmp_path: Path) -> Path:
    led = tmp_path / "main.bean"
    led.write_text(BROKEN)
    return led


def _mentions_both(items: list[dict[str, object]] | list[str]) -> bool:
    text = json.dumps(items)
    return "acounts.bean" in text and "main.bean:" in text and len(items) >= 2


def test_load_turns_errors_into_violations(tmp_path: Path) -> None:
    entries, problems = ledger.load(_broken(tmp_path))
    assert entries  # what parsed still loads
    messages = [v.message for v in problems]
    assert any("acounts.bean" in m for m in messages)
    assert any("main.bean:" in m and "acounts.bean" not in m for m in messages)


def test_vat_report_lists_loader_errors(tmp_path: Path) -> None:
    r = mwst.compute(_broken(tmp_path), "2026-07-01", "2026-09-30", config.Config())
    assert r.z303_net == 1000  # the short return still renders …
    assert any("acounts.bean" in v.message for v in r.violations)  # … and says why


def test_failing_balance_assertion_does_not_block_reports(tmp_path: Path) -> None:
    led = tmp_path / "main.bean"
    led.write_text(
        BROKEN.split("\n2026-07-03")[0].replace('include "acounts.bean"\n', "")
        + "\n2026-08-01 balance Assets:CH:GmbH:Current:UBS:CHF  5.00 CHF\n"
    )
    r = mwst.compute(led, "2026-07-01", "2026-09-30", config.Config())
    assert r.z303_net == 1000
    (v,) = r.violations
    assert "Balance failed" in v.message and v.date == "2026-08-01"
    b = kmu.compute_bilanz(led, "2026-12-31", config.Config())
    assert b.total_assets == 1081 and len(b.violations) == 1


def test_statements_pdf_lists_loader_errors_once(tmp_path: Path) -> None:
    led = _broken(tmp_path)
    b = kmu.compute_bilanz(led, "2026-12-31", config.Config())
    e = kmu.compute_erfolg(led, "2026-01-01", "2026-12-31", config.Config())
    ctx = report_pdf.build_context(b, e, "en")
    problems = ctx["violations"]
    assert isinstance(problems, list) and len(problems) == len(b.violations) >= 2


def test_import_says_dedup_is_unreliable(tmp_path: Path) -> None:
    cfg = config.Config(import_ubs=config.UbsImport(iban="CH9300762011623852957"))
    result = importing.run_ubs(
        FIXTURES / "transactions.mt940", _broken(tmp_path), out_dir=tmp_path / "s", cfg=cfg
    )
    assert any("acounts.bean" in v.message for v in result.violations)


def _items(key: str) -> Callable[[dict[str, object]], object]:
    def get(d: dict[str, object]) -> object:
        node: object = d
        for part in key.split("."):
            assert isinstance(node, dict)
            node = node[part]  # pyright: ignore[reportUnknownVariableType] -- parsed JSON
        return node

    return get


# Every command that turns the books into money: (argv, where --json lists them).
COMMANDS = [
    (["vat", "report", "-p", "2026-Q3"], "violations"),
    (["vat", "settle", "-p", "2026-Q3"], "report.violations"),
    (["vat", "status"], "violations"),
    (["vat", "liability", "--at", "2026-12-31"], "violations"),
    (["report", "bilanz", "--at", "2026-12-31"], "violations"),
    (["report", "erfolg", "--year", "2026"], "violations"),
    (["report", "konten", "--year", "2026"], "violations"),
    (["receivables", "--at", "2026-12-31"], "violations"),
    (["payables", "--at", "2026-12-31"], "violations"),
    (["close", "depreciation", "--year", "2026"], "warnings"),
    (["fx", "revalue", "--at", "2026-12-31"], "violations"),
]


@pytest.mark.parametrize(("argv", "key"), COMMANDS, ids=[" ".join(a[:2]) for a, _ in COMMANDS])
def test_cli_reports_render_and_list_loader_errors(
    tmp_path: Path, argv: list[str], key: str
) -> None:
    led = _broken(tmp_path)
    res = runner.invoke(app, [*argv, "-f", str(led)])
    assert res.exit_code == 0, res.output  # bad books are reported, not refused
    assert "acounts.bean" in res.output

    res = runner.invoke(app, [*argv, "--json", "-f", str(led)])
    assert res.exit_code == 0, res.output
    items = _items(key)(json.loads(res.stdout))
    assert isinstance(items, list) and _mentions_both(items)  # pyright: ignore[reportUnknownArgumentType] -- parsed JSON


def test_cli_import_warns_dedup_unreliable(tmp_path: Path) -> None:
    led = _broken(tmp_path)
    toml = tmp_path / "quints.toml"
    toml.write_text('[import.ubs]\niban = "CH9300762011623852957"\n')
    argv = ["--config", str(toml), "import", "ubs", str(FIXTURES / "transactions.mt940")]
    res = runner.invoke(app, [*argv, "--out", str(tmp_path / "s"), "-f", str(led)])
    assert res.exit_code == 0, res.output
    assert "acounts.bean" in res.output and "dedup" in res.output

    res = runner.invoke(app, [*argv, "--out", str(tmp_path / "s2"), "--json", "-f", str(led)])
    assert res.exit_code == 0, res.output
    assert _mentions_both(json.loads(res.stdout)["violations"])


def test_cli_statements_pdf_renders_with_loader_errors(tmp_path: Path) -> None:
    led = _broken(tmp_path)
    out = tmp_path / "s.pdf"
    res = runner.invoke(
        app, ["report", "statements", "--year", "2026", "-o", str(out), "-f", str(led)]
    )
    assert res.exit_code == 0, res.output
    assert out.exists() and "acounts.bean" in res.output


def test_prices_sync_fails_on_unusable_price_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken `price:` spec used to warn and exit 0 — the currency silently
    went unfetched. The usable jobs still sync; the run then exits 1."""
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
        '2024-01-01 commodity GBP\n  price: "nonsense"\n'
    )
    (tmp_path / "quints.toml").write_text(f'[ledger]\nmain = "{tmp_path}/main.bean"\n')
    seeded = datetime.now(timezone.utc).date() - timedelta(days=2)
    out = tmp_path / "prices.bean"
    out.write_text(f"; quints: verified EUR/CHF 2024-01-01..{seeded}\n")
    argv = ["--config", str(tmp_path / "quints.toml"), "prices", "sync", "--out", str(out)]
    res = runner.invoke(app, [*argv, "--json"])
    assert res.exit_code == 1, res.output
    d = json.loads(res.stdout)
    assert list(d["per_currency"]) == ["EUR"] and d["added"] == 2  # the usable job ran
    assert any("GBP" in w for w in d["warnings"])


def test_fx_revalue_names_the_loader_error_behind_a_missing_rate(tmp_path: Path) -> None:
    """The include that held the price lines is gone: the rate error alone
    would hide why, so the loader error is listed first."""
    led = tmp_path / "main.bean"
    led.write_text(
        'include "prices.bean"\n'
        "2026-01-01 open Assets:CH:GmbH:Current:Wise:EUR EUR\n"
        "2026-01-01 open Equity:CH:GmbH:Capital EUR\n"
        '2026-07-02 * "Owner" "capital"\n'
        "  Assets:CH:GmbH:Current:Wise:EUR   100.00 EUR @ 0.93 CHF\n"
        "  Equity:CH:GmbH:Capital           -93.00 CHF\n"
    )
    res = runner.invoke(app, ["fx", "revalue", "--at", "2026-12-31", "-f", str(led)])
    assert res.exit_code == 1, res.output
    assert "prices.bean" in res.output and "rate" in res.output


def test_vat_convert_json_lists_loader_errors(tmp_path: Path) -> None:
    led = tmp_path / "main.bean"
    led.write_text('include "acounts.bean"\n2026-07-01 price EUR 0.93 CHF\n')
    argv = ["vat", "convert", "100", "EUR", "2026-07-02", "--json", "-f", str(led)]
    res = runner.invoke(app, argv)
    assert res.exit_code == 0, res.output
    d = json.loads(res.stdout)
    assert d["chf"] == "93.00" and "acounts.bean" in json.dumps(d["violations"])


def test_invoice_cross_check_lists_loader_errors(tmp_path: Path) -> None:
    issuer = tmp_path / "issuer.yaml"
    issuer.write_text(
        "name: Muster GmbH\naddress: [Musterstrasse 1, 3000 Bern]\n"
        "vat_id: CHE-267.359.056 MWST\n"
        "bank:\n  CHF:\n    iban: CH93 0076 2011 6238 5295 7\n"
    )
    inv = tmp_path / "inv.yaml"
    inv.write_text(
        "number: INV2026014\nkind: domestic\ncurrency: CHF\nissue_date: 2026-07-02\n"
        "supply: 2026-06\ncustomer: {name: Acme AG, address: [Bahnhofstrasse 1, 8001 Zürich]}\n"
        "items:\n  - {description: Consulting, quantity: 1, unit_price: 1000.00}\n"
    )
    led = _broken(tmp_path)
    argv = ["invoice", str(inv), "--issuer", str(issuer), "--out", str(tmp_path / "o.pdf")]
    res = runner.invoke(app, [*argv, "-f", str(led)])
    assert res.exit_code == 0, res.output
    assert "acounts.bean" in res.output
    res = runner.invoke(app, [*argv, "--json", "-f", str(led)])
    assert res.exit_code == 0, res.output
    assert _mentions_both(json.loads(res.stdout)["cross_check"]["violations"])
