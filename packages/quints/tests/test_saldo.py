"""The Saldosteuersatz method (Art. 37 MWSTG) end to end.

The Ziffern follow MWST-Info 12, Ziff. 18.1 (Stand 01.01.2025): Teil I is the
same 200–299 as the effective method, Teil II is 322/323 (the granted rates,
split on the Beiblatt) plus 383/382 Bezugsteuer, and there is no input-VAT
block at all. The permitted rates are the ordinance's list, SR 641.202.62.
"""

from __future__ import annotations

import json
import re
from datetime import date as Date
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quints import config, init, ledger, mwst, settlement
from quints.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolate_process_config():
    """Drop the process-wide config a chdir'd CLI run resolved and cached.

    `config.get()` memoises `./quints.toml`; without this a saldo project left
    behind by one test would silently become the default for the next one.
    """
    yield
    config.set_path(None)


CFG = config.validate(
    config.Config(
        vat_method="saldo",
        saldo=(
            config.SaldoRate(Decimal("0.062")),  # the default: consulting
            config.SaldoRate(Decimal("0.013"), marker=":Handel"),
        ),
    )
)

_CHART = """
2026-01-01 open Assets:CH:GmbH:Current:UBS:CHF CHF
  kmu: "1020"
2026-01-01 open Assets:CH:GmbH:Current:Wise:EUR EUR
  kmu: "1020"
2026-01-01 open Assets:CH:GmbH:Receivable:Trade
  kmu: "1100"
2026-01-01 open Assets:CH:GmbH:Tax:InputVAT CHF
  kmu: "1170"
2026-01-01 open Liabilities:CH:GmbH:Tax:OutputVAT CHF
  kmu: "2200"
2026-01-01 open Liabilities:CH:GmbH:Tax:Bezugsteuer CHF
  kmu: "2200"
2026-01-01 open Liabilities:CH:GmbH:Tax:PayableVAT CHF
  kmu: "2200"
2026-01-01 open Income:CH:GmbH:Consulting:External:Domestic CHF
  kmu: "3400"
2026-01-01 open Income:CH:GmbH:Trade:Handel CHF
  kmu: "3200"
2026-01-01 open Income:CH:GmbH:Consulting:External:Export
  kmu: "3400"
2026-01-01 open Income:CH:GmbH:Erloesminderungen CHF
  kmu: "3800"
2026-01-01 open Income:CH:GmbH:VAT:SaldoDifference CHF
  kmu: "3600"
2026-01-01 open Expenses:CH:GmbH:Tax:Bezugsteuer CHF
  kmu: "6700"
2026-01-01 open Expenses:CH:GmbH:IT:Hosting
  kmu: "6570"

2026-07-01 price EUR 0.93 CHF
"""

# Two granted rates, an export, a credit note and a reverse-charge purchase.
_BOOKS = """
2026-07-02 * "Acme AG" "Consulting"
  Assets:CH:GmbH:Receivable:Trade                1081.00 CHF
  Income:CH:GmbH:Consulting:External:Domestic   -1000.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT               -81.00 CHF

2026-07-05 * "Laden" "Handel"
  Assets:CH:GmbH:Current:UBS:CHF                  540.50 CHF
  Income:CH:GmbH:Trade:Handel                    -500.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT               -40.50 CHF

2026-07-10 * "Globex Ltd" "Export consulting"
  Assets:CH:GmbH:Receivable:Trade                 200.00 EUR
  Income:CH:GmbH:Consulting:External:Export      -200.00 EUR

2026-07-20 * "Acme AG" "Skonto"
  Income:CH:GmbH:Erloesminderungen                 20.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT                 1.62 CHF
  Assets:CH:GmbH:Receivable:Trade                 -21.62 CHF

2026-08-12 * "Foreign SaaS" "Cloud hosting (reverse charge)"
  Expenses:CH:GmbH:IT:Hosting                     100.00 EUR
  Expenses:CH:GmbH:Tax:Bezugsteuer                  7.53 CHF @@ 8.10 EUR
  Liabilities:CH:GmbH:Tax:Bezugsteuer              -7.53 CHF @@ 8.10 EUR
  Assets:CH:GmbH:Current:Wise:EUR                -100.00 EUR
"""


def _compute(tmp_path: Path, books: str = _BOOKS, cfg: config.Config = CFG) -> mwst.MwstReport:
    led = tmp_path / "m.bean"
    led.write_text(_CHART + books)
    return mwst.compute(led, *mwst.period_range("2026-H2"), cfg)


def _rows(report: mwst.MwstReport) -> dict[str, mwst.RateRow]:
    return {f"{r.ziffer}/{r.label}": r for r in report.rate_rows}


# ── the permitted rates are law ───────────────────────────────────────────────


def test_permitted_rates_are_date_ranged() -> None:
    # SR 641.202.62 (vom 5.9.2024, Stand 1.1.2025); the 2024 step came with
    # the rate increase, and the ESTV published the pairs old → new.
    def percents(on: Date) -> list[str]:
        return [str((r * 100).quantize(Decimal("0.1"))) for r in ledger.saldo_rates(on)]

    assert " ".join(percents(Date(2026, 1, 1))) == "0.1 0.6 1.3 2.1 3.0 3.7 4.5 5.3 6.2 6.8"
    assert " ".join(percents(Date(2023, 6, 1))) == "0.1 0.6 1.2 2.0 2.8 3.5 4.3 5.1 5.9 6.5"
    assert ledger.is_saldo_rate(Decimal("0.062"))
    assert ledger.is_saldo_rate(Decimal("0.059"))  # a pre-2024 grant still counts
    assert not ledger.is_saldo_rate(Decimal("0.05"))


def test_config_rejects_a_rate_the_estv_cannot_grant() -> None:
    with pytest.raises(config.ConfigError, match=re.escape("SR 641.202.62")):
        config.validate(
            config.Config(vat_method="saldo", saldo=(config.SaldoRate(Decimal("0.05")),))
        )


def test_config_wants_rates_for_saldo_and_none_otherwise() -> None:
    with pytest.raises(config.ConfigError, match="granted"):
        config.validate(config.Config(vat_method="saldo"))
    with pytest.raises(config.ConfigError, match="not"):
        config.validate(config.Config(saldo=(config.SaldoRate(Decimal("0.062")),)))
    with pytest.raises(config.ConfigError, match="unknown"):
        config.validate(config.Config(vat_method="flat"))
    with pytest.raises(config.ConfigError, match="public bodies"):
        config.validate(config.Config(vat_method="pauschal"))


def test_period_defaults_follow_the_method() -> None:
    # Art. 35 MWSTG: the effective method files quarterly, SSS half-yearly.
    assert config.Config().period_kind == "quarter"
    assert CFG.period_kind == "half-year"
    assert config.validate(config.replace(CFG, vat_period="year")).period_kind == "year"


def test_toml_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "quints.toml"
    path.write_text(
        '[entity]\nvat_method = "saldo"\n\n[vat]\nperiod = "year"\n\n'
        '[[vat.saldo]]\nrate = 6.2\n\n[[vat.saldo]]\nrate = 1.3\nmarker = ":Handel"\n'
    )
    cfg = config.load(path)
    assert cfg.period_kind == "year"
    assert [g.name for g in cfg.saldo] == ["6.2", "1.3"]
    assert cfg.saldo[1].marker == ":Handel"
    assert cfg.saldo_default is not None and cfg.saldo_default.name == "6.2"


# ── periods ───────────────────────────────────────────────────────────────────


def test_period_parser() -> None:
    assert mwst.period_range("2026-Q2") == ("2026-04-01", "2026-06-30")
    assert mwst.period_range("2026-H1") == ("2026-01-01", "2026-06-30")
    assert mwst.period_range("2026-H2") == ("2026-07-01", "2026-12-31")
    assert mwst.period_range("2026") == ("2026-01-01", "2026-12-31")
    assert mwst.period_range("2026h2") == mwst.period_range("2026-H2")
    assert mwst.period_label("2026h2") == "2026-H2"
    assert mwst.period_label("2026") == "2026"
    for bad in ("2026-H3", "2026-X1", "nope"):
        with pytest.raises(ValueError, match="period"):
            mwst.period_range(bad)


# ── the report ────────────────────────────────────────────────────────────────


def test_section_one_is_gross(tmp_path: Path) -> None:
    r = _compute(tmp_path)
    # Under SSS the declared values include MWST (MWST-Info 12, Ziff. 18.1.1):
    # 1000 + 500 net, + 81.00 + 40.50 VAT, + 186.00 export.
    assert r.z200 == Decimal("1807.50")
    assert r.z221 == Decimal("186.00")  # 200 EUR @ 0.93, no VAT on an export
    assert r.z235 == Decimal("21.62")  # the Skonto and the VAT it reversed
    assert r.z289 == Decimal("207.62")
    assert r.z299 == Decimal("1599.88")
    assert r.vat_method == "saldo" and "MWST-Info 12" in r.form


def test_turnover_rows_equal_ziffer_299(tmp_path: Path) -> None:
    r = _compute(tmp_path)
    assert sum((row.net for row in r.rate_rows), Decimal("0")) == r.z299
    assert r.violations == []


def test_one_row_per_granted_rate(tmp_path: Path) -> None:
    r = _compute(tmp_path)
    rows = _rows(r)
    # The consulting sale less the Skonto, both at the default 6.2 %.
    assert rows["323/6.2"].net == Decimal("1059.38")
    assert rows["323/6.2"].tax == Decimal("65.68")  # 1059.38 x 6.2 %
    # The marked trade account gets the second granted rate.
    assert rows["323/1.3"].net == Decimal("540.50")
    assert rows["323/1.3"].tax == Decimal("7.03")
    assert r.z323_net == Decimal("1599.88") and r.z323_tax == Decimal("72.71")
    assert r.z322_net == r.z322_tax == Decimal("0")


def test_bezugsteuer_is_owed_but_not_deducted(tmp_path: Path) -> None:
    r = _compute(tmp_path)
    assert r.z383_tax == Decimal("7.53")  # statutory rate, Ziffer 383
    assert r.z400 == r.z405 == r.z479 == Decimal("0")  # the form has no such lines
    assert r.z500 == Decimal("80.24")  # 65.68 + 7.03 + 7.53
    assert r.z510 == Decimal("0")


def test_old_vintage_files_under_322(tmp_path: Path) -> None:
    r = _compute(
        tmp_path,
        """
2026-09-10 * "Altkunde" "2023 supply, invoiced now"
  mwst: "old_rate"
  Assets:CH:GmbH:Current:UBS:CHF                 1077.00 CHF
  Income:CH:GmbH:Consulting:External:Domestic   -1000.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT               -77.00 CHF
""",
    )
    rows = _rows(r)
    assert "322/6.2" in rows and rows["322/6.2"].net == Decimal("1077.00")
    assert r.z322_tax == Decimal("66.77")  # 1077.00 x 6.2 %
    assert r.violations == []


def test_input_vat_under_saldo_is_a_violation(tmp_path: Path) -> None:
    r = _compute(
        tmp_path,
        """
2026-08-15 * "Lieferant" "Material, wrongly split"
  Expenses:CH:GmbH:IT:Hosting                     200.00 CHF
  Assets:CH:GmbH:Tax:InputVAT                      16.20 CHF
  Assets:CH:GmbH:Current:UBS:CHF                 -216.20 CHF
""",
    )
    (v,) = r.violations
    assert "not deductible under the Saldosteuersatz method" in v.message
    assert r.z400 == Decimal("0")  # and it is not smuggled into a deduction


def test_sss_token_pins_a_rate_and_a_typo_is_reported(tmp_path: Path) -> None:
    r = _compute(
        tmp_path,
        """
2026-07-02 * "Acme AG" "Consulting, billed as trade"
  mwst: "sss=1.3"
  Assets:CH:GmbH:Current:UBS:CHF                  108.10 CHF
  Income:CH:GmbH:Consulting:External:Domestic    -100.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT                -8.10 CHF

2026-07-03 * "Acme AG" "Typo in the tag"
  mwst: "sss=9.9"
  Assets:CH:GmbH:Current:UBS:CHF                  108.10 CHF
  Income:CH:GmbH:Consulting:External:Domestic    -100.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT                -8.10 CHF
""",
    )
    rows = _rows(r)
    assert rows["323/1.3"].net == Decimal("108.10")
    assert rows["323/6.2"].net == Decimal("108.10")  # the typo falls to the default
    (v,) = r.violations
    assert v.message.startswith('unknown mwst: token "sss=9.9"')


def test_output_vat_without_turnover_is_reported(tmp_path: Path) -> None:
    r = _compute(
        tmp_path,
        """
2026-07-02 * "Acme AG" "VAT with no income leg"
  Assets:CH:GmbH:Current:UBS:CHF                   81.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT               -81.00 CHF
""",
    )
    (v,) = r.violations
    assert "the SSS form taxes the gross Entgelt" in v.message


# ── settlement ────────────────────────────────────────────────────────────────


def test_settlement_books_the_difference_and_round_trips(tmp_path: Path) -> None:
    led = tmp_path / "m.bean"
    led.write_text(_CHART + _BOOKS)
    r = mwst.compute(led, *mwst.period_range("2026-H2"), CFG)
    s = settlement.build_settlement(led, r, mwst.period_label("2026-H2"), CFG)

    assert s.link == "VAT-2026-H2"
    assert s.due == "2027-03-01"  # period end + 60 days (Art. 86 MWSTG)
    assert s.output_vat == Decimal("119.88")  # the statutory VAT invoiced
    assert s.net == Decimal("80.24")  # the SSS owed plus Bezugsteuer
    assert s.difference == Decimal("47.17")  # what the method leaves behind
    assert -s.net + s.output_vat + s.bezugsteuer - s.difference == 0

    text = settlement.settlement_text(s, CFG)
    assert "Income:CH:GmbH:VAT:SaldoDifference" in text
    assert "InputVAT" not in text  # no deduction, and nothing to assert

    # Paste it back: the ledger must still load and the assertions must hold.
    led.write_text(_CHART + _BOOKS + "\n" + text + "\n")
    _entries, errors = ledger.load_entries(led)
    assert not errors, errors

    # And the settled period reports nothing left over.
    after = mwst.compute(led, *mwst.period_range("2026-H2"), CFG)
    assert after.z299 == r.z299 and after.violations == []


def test_convert_bezugsteuer_books_a_cost_under_saldo(tmp_path: Path) -> None:
    from beancount.core import prices as bc_prices

    from quints import vat as vat_mod

    led = tmp_path / "m.bean"
    led.write_text(_CHART)
    entries, _errors = ledger.load_entries(led)
    posting = vat_mod.convert(
        Decimal("100"),
        "EUR",
        Date(2026, 7, 2),
        bc_prices.build_price_map(entries),
        net=True,
        cfg=CFG,
    )
    pair = posting.render_bezugsteuer()
    assert "Expenses:CH:GmbH:Tax:Bezugsteuer" in pair
    assert "Tax:InputVAT" not in pair
    # Plain `vat convert` has nothing to post: the gross price is the expense.
    assert "not deductible" in posting.render()


# ── the scaffold ──────────────────────────────────────────────────────────────


def test_init_accepts_saldo_and_validates_the_rate() -> None:
    files = {
        f.path.name: f.content
        for f in init.plan(init.Answers(vat_method="saldo", saldo_rates=("6.2",)))
    }
    assert 'vat_method = "saldo"' in files["quints.toml"]
    assert "[[vat.saldo]]\nrate = 6.2" in files["quints.toml"]
    assert 'period = "half-year"' in files["quints.toml"]
    assert "Income:CH:GmbH:VAT:SaldoDifference" in files["accounts.bean"]
    assert "Expenses:CH:GmbH:Tax:Bezugsteuer" in files["accounts.bean"]
    with pytest.raises(init.InitError, match=re.escape("SR 641.202.62")):
        init.plan(init.Answers(vat_method="saldo", saldo_rates=("5.0",)))
    with pytest.raises(init.InitError, match="granted"):
        init.plan(init.Answers(vat_method="saldo"))
    with pytest.raises(init.InitError, match="only meaningful"):
        init.plan(init.Answers(saldo_rates=("6.2",)))


def test_saldo_agents_playbook_says_book_gross() -> None:
    saldo = {f.path.name: f.content for f in init.plan(_saldo_answers())}["AGENTS.md"]
    assert "Saldosteuersatz method" in saldo and "book purchases **gross**" in saldo
    plain = {f.path.name: f.content for f in init.plan(init.Answers())}["AGENTS.md"]
    assert "Saldosteuersatz" not in plain  # the effective playbook is untouched


def _saldo_answers() -> init.Answers:
    return init.Answers(
        entity_name="Jane Doe",
        legal_form="einzelfirma",
        vat_method="saldo",
        saldo_rates=("6.2",),
        include_samples=True,
    )


def test_scaffolded_saldo_project_reports_and_settles(tmp_path: Path) -> None:
    # The demo quarter, under SSS: gross turnover, no input VAT, the reverse
    # charge booked as a cost.
    init.write(tmp_path, init.plan(_saldo_answers()))
    main = tmp_path / "main.bean"
    _entries, errors = ledger.load_entries(main)
    assert not errors, errors
    cfg = config.load(tmp_path / "quints.toml")
    assert cfg.vat_method == "saldo" and cfg.period_kind == "half-year"

    r = mwst.compute(main, *mwst.period_range("2026-H2"), cfg)
    assert r.z200 == Decimal("1551.00")  # 1000 net + 81 MWST + 470 export
    assert r.z221 == Decimal("470.00")
    assert r.z299 == Decimal("1081.00")
    assert r.z323_tax == Decimal("67.02")  # 1081.00 x 6.2 %
    assert r.z383_tax == Decimal("7.53")
    assert r.z500 == Decimal("74.55")
    assert r.violations == []


def test_cli_saldo_flow_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The steps a saldo user types: scaffold, report, settle."""
    answers = tmp_path / "answers.toml"
    answers.write_text(
        'entity_name = "Jane Doe"\nlegal_form = "einzelfirma"\n'
        'vat_method = "saldo"\nsaldo_rates = ["6.2"]\ninclude_samples = true\n'
    )
    proj = tmp_path / "books"
    res = runner.invoke(app, ["init", str(proj), "--answers", str(answers), "--no-git"])
    assert res.exit_code == 0, res.output

    monkeypatch.chdir(proj)
    res = runner.invoke(app, ["vat", "report", "-p", "2026-H2", "--json"])
    assert res.exit_code == 0, res.output
    d = json.loads(res.output)
    assert d["vat_method"] == "saldo" and "MWST-Info 12" in d["form"]
    assert Decimal(d["z200"]) == Decimal("1551.00")
    assert Decimal(d["z299"]) == Decimal("1081.00")
    assert d["z323_tax"] == "67.02" and d["z500"] == "74.55"
    assert d["z479"] == "0" and d["violations"] == []
    (row,) = d["rate_rows"]
    assert row["ziffer"] == "323" and row["label"] == "6.2" and row["rate_class"] == "saldo"

    res = runner.invoke(app, ["vat", "settle", "-p", "2026-H2", "--json"])
    assert res.exit_code == 0, res.output
    s = json.loads(res.output)["settlement"]
    assert s["link"] == "VAT-2026-H2" and s["method"] == "saldo"
    assert s["difference"] == "13.98"
    assert "VAT:SaldoDifference" in s["text"]

    # `vat status` tells an agent how often these books file.
    res = runner.invoke(app, ["vat", "status", "--json"])
    assert res.exit_code == 0, res.output
    status = json.loads(res.output)
    assert status["vat_method"] == "saldo" and status["period"] == "half-year"
