"""A company's VAT life: not registered, registered, switching method, deregistered.

The situation is not a constant. A business starts below the threshold and
registers later (Art. 10/14 MWSTG), switches between the effective and the
Saldosteuersatz method at the start of a tax period (Art. 37 Abs. 4 MWSTG),
may move to annual filing (Art. 35a MWSTG) and eventually deregisters. Every
period is computed under the rules in force for it.
"""

from __future__ import annotations

import json
from datetime import date as Date
from decimal import Decimal
from pathlib import Path

import pytest
from beancount.core import prices as bc_prices
from typer.testing import CliRunner

from quints import closing, config, init, liability, mwst, settlement, vat
from quints.cli import app
from quints.invoice import render, swico
from quints.invoice.model import Invoice, Issuer, LineItem, Party, SupplyPeriod, compute

runner = CliRunner()

SSS = (config.SaldoRate(Decimal("0.062")),)


@pytest.fixture(autouse=True)
def isolate_process_config():
    yield
    config.set_path(None)


def _toml(tmp_path: Path, text: str) -> config.Config:
    path = tmp_path / "quints.toml"
    path.write_text(text)
    return config.load(path)


# ── the timeline ─────────────────────────────────────────────────────────────


def test_a_change_of_method_is_a_dated_phase(tmp_path: Path) -> None:
    cfg = _toml(
        tmp_path,
        "[entity]\nvat_registered_since = 2022-03-01\n\n"
        '[[vat.change]]\nfrom = 2026-01-01\nmethod = "saldo"\nsaldo = [{ rate = 6.2 }]\n\n'
        '[[vat.change]]\nfrom = 2027-01-01\nperiod = "year"\n',
    )
    first, sss, annual = cfg.vat_phases
    assert (first.start, first.end, first.method, first.period) == (
        Date(2022, 3, 1),
        Date(2025, 12, 31),
        "effective",
        "quarter",
    )
    # A switch resets the filing period to the new method's default…
    assert (sss.method, sss.period, sss.saldo) == ("saldo", "half-year", SSS)
    # …and a later change inherits what it does not name.
    assert (annual.method, annual.period, annual.saldo, annual.end) == ("saldo", "year", SSS, None)
    assert cfg.phase_at(Date(2025, 6, 1)) == first
    assert cfg.phase_at(Date(2022, 2, 28)) is None


def test_changes_take_effect_on_a_first_of_january(tmp_path: Path) -> None:
    with pytest.raises(config.ConfigError, match="1 January"):
        _toml(tmp_path, '[[vat.change]]\nfrom = 2026-07-01\nperiod = "year"\n')


def test_changes_are_listed_oldest_first(tmp_path: Path) -> None:
    with pytest.raises(config.ConfigError, match="oldest first"):
        _toml(
            tmp_path,
            '[[vat.change]]\nfrom = 2027-01-01\nperiod = "year"\n\n'
            '[[vat.change]]\nfrom = 2026-01-01\nperiod = "quarter"\n',
        )


def test_a_change_must_change_something(tmp_path: Path) -> None:
    with pytest.raises(config.ConfigError, match="changes nothing"):
        _toml(tmp_path, "[[vat.change]]\nfrom = 2027-01-01\n")


def test_a_switch_to_saldo_needs_the_granted_rate(tmp_path: Path) -> None:
    with pytest.raises(config.ConfigError, match="lists no Saldosteuersatz"):
        _toml(
            tmp_path,
            "[entity]\nvat_registered_since = 2020-01-01\n\n"
            '[[vat.change]]\nfrom = 2026-01-01\nmethod = "saldo"\n',
        )


def test_the_effective_method_is_kept_three_years(tmp_path: Path) -> None:
    # Art. 37 Abs. 4 MWSTG: effective → saldo no earlier than after three years.
    change = '[[vat.change]]\nfrom = {}\nmethod = "saldo"\nsaldo = [{{ rate = 6.2 }}]\n'
    with pytest.raises(config.ConfigError, match="earliest is 2027-01-01"):
        _toml(
            tmp_path,
            "[entity]\nvat_registered_since = 2024-01-01\n\n" + change.format("2026-01-01"),
        )
    ok = _toml(
        tmp_path, "[entity]\nvat_registered_since = 2024-01-01\n\n" + change.format("2027-01-01")
    )
    assert ok.vat_phases[1].method == "saldo"


def test_the_saldo_method_is_kept_one_tax_period(tmp_path: Path) -> None:
    head = (
        '[entity]\nvat_method = "saldo"\nvat_registered_since = {}\n\n[[vat.saldo]]\nrate = 6.2\n\n'
    )
    back = '[[vat.change]]\nfrom = {}\nmethod = "effective"\n'
    # One tax period — even the short first one after a mid-year registration.
    ok = _toml(tmp_path, head.format("2026-06-01") + back.format("2027-01-01"))
    assert [p.method for p in ok.vat_phases] == ["saldo", "effective"]
    assert ok.vat_phases[1].saldo == ()


def test_config_earliest_switch_counts_from_the_start() -> None:
    assert config.earliest_switch("effective", Date(2024, 1, 1)) == Date(2027, 1, 1)
    assert config.earliest_switch("effective", Date(2024, 4, 1)) == Date(2028, 1, 1)
    assert config.earliest_switch("saldo", Date(2026, 1, 1)) == Date(2027, 1, 1)


def test_deregistration_closes_the_last_phase(tmp_path: Path) -> None:
    cfg = _toml(
        tmp_path, "[entity]\nvat_registered_since = 2024-01-01\nvat_registered_until = 2026-06-30\n"
    )
    assert cfg.vat_phases[-1].end == Date(2026, 6, 30)
    assert cfg.liable_on(Date(2026, 6, 30)) and not cfg.liable_on(Date(2026, 7, 1))
    with pytest.raises(config.ConfigError, match="before vat_registered_since"):
        _toml(
            tmp_path,
            "[entity]\nvat_registered_since = 2026-01-01\nvat_registered_until = 2025-12-31\n",
        )


def test_not_registered_has_no_phase() -> None:
    cfg = config.Config(vat_registered=False)
    assert cfg.vat_phases == () and not cfg.liable_on(Date(2026, 1, 1))


# ── returns follow the phase in force ────────────────────────────────────────

_CHART = """
2020-01-01 open Assets:CH:GmbH:Current:UBS:CHF CHF
2020-01-01 open Assets:CH:GmbH:Receivable:Trade
2020-01-01 open Assets:CH:GmbH:Tax:InputVAT CHF
2020-01-01 open Liabilities:CH:GmbH:Tax:OutputVAT CHF
2020-01-01 open Liabilities:CH:GmbH:Tax:PayableVAT CHF
2020-01-01 open Income:CH:GmbH:Consulting:External:Domestic
2020-01-01 open Expenses:CH:GmbH:IT:Hosting CHF

2025-05-02 * "Kunde" "2025 sale"
  Assets:CH:GmbH:Receivable:Trade                1081.00 CHF
  Income:CH:GmbH:Consulting:External:Domestic   -1000.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT               -81.00 CHF

2025-05-03 * "Hoster" "2025 purchase"
  Expenses:CH:GmbH:IT:Hosting                      100.00 CHF
  Assets:CH:GmbH:Tax:InputVAT                        8.10 CHF
  Assets:CH:GmbH:Current:UBS:CHF                  -108.10 CHF

2026-05-02 * "Kunde" "2026 sale"
  Assets:CH:GmbH:Receivable:Trade                1081.00 CHF
  Income:CH:GmbH:Consulting:External:Domestic   -1000.00 CHF
  Liabilities:CH:GmbH:Tax:OutputVAT               -81.00 CHF
"""

_SWITCH = config.Config(
    vat_registered_since=Date(2022, 1, 1),
    vat_changes=(config.VatChange(Date(2026, 1, 1), method="saldo", saldo=SSS),),
)


def _ledger(tmp_path: Path) -> Path:
    path = tmp_path / "main.bean"
    path.write_text(_CHART)
    return path


def test_each_period_is_computed_under_its_own_method(tmp_path: Path) -> None:
    books = _ledger(tmp_path)
    before = mwst.compute(books, "2025-04-01", "2025-06-30", cfg=_SWITCH)
    assert before.vat_method == "effective" and before.z400 + before.z405 == Decimal("8.10")
    after = mwst.compute(books, "2026-01-01", "2026-06-30", cfg=_SWITCH)
    assert after.vat_method == "saldo"
    (row,) = after.rate_rows
    assert row.ziffer == "323" and row.net == Decimal("1081.00")


def test_the_switch_is_announced_on_both_sides(tmp_path: Path) -> None:
    books = _ledger(tmp_path)
    last = mwst.compute(books, "2025-10-01", "2025-12-31", cfg=_SWITCH)
    assert any("Last return under the effective method" in n for n in last.notices)
    assert any("vorsteuerkorrektur" in n for n in last.notices)
    first = mwst.compute(books, "2026-01-01", "2026-06-30", cfg=_SWITCH)
    assert any("First return under the saldo method" in n for n in first.notices)
    quiet = mwst.compute(books, "2025-04-01", "2025-06-30", cfg=_SWITCH)
    assert quiet.notices == []


def test_a_return_cannot_span_a_switch(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="spans a VAT change on 2026-01-01"):
        mwst.compute(_ledger(tmp_path), "2025-07-01", "2026-06-30", cfg=_SWITCH)


def test_first_and_final_returns_are_clamped_and_announced(tmp_path: Path) -> None:
    cfg = config.Config(
        vat_registered_since=Date(2025, 5, 3), vat_registered_until=Date(2026, 5, 31)
    )
    first = mwst.compute(_ledger(tmp_path), "2025-04-01", "2025-06-30", cfg=cfg)
    assert (first.liable_from, first.liable_to) == ("2025-05-03", "2025-06-30")
    assert first.z303_tax == 0  # the 2 May sale predates liability
    assert any("Einlageentsteuerung" in n for n in first.notices)
    final = mwst.compute(_ledger(tmp_path), "2026-04-01", "2026-06-30", cfg=cfg)
    assert final.liable_to == "2026-05-31"
    assert any("Final return" in n and "Eigenverbrauch" in n for n in final.notices)


def test_no_return_outside_liability(tmp_path: Path) -> None:
    with pytest.raises(config.NotLiable, match="not VAT-registered"):
        mwst.compute(
            _ledger(tmp_path),
            "2025-01-01",
            "2025-03-31",
            cfg=config.Config(vat_registered_since=Date(2025, 6, 1)),
        )
    with pytest.raises(config.NotLiable, match="vat liability"):
        mwst.compute(
            _ledger(tmp_path), "2025-01-01", "2025-03-31", cfg=config.Config(vat_registered=False)
        )


def test_close_check_expects_the_grid_of_each_year(tmp_path: Path) -> None:
    assert closing.vat_periods(2025, _SWITCH)[-1] == "VAT-2025-Q4"
    assert closing.vat_periods(2026, _SWITCH) == ["VAT-2026-H1", "VAT-2026-H2"]


def test_convert_follows_the_method_of_the_invoice_date() -> None:
    pm: bc_prices.PriceMap = bc_prices.build_price_map([])
    cfg = config.Config(vat_registered_since=Date(2026, 3, 1))
    before = vat.convert(Decimal("8.10"), "CHF", Date(2026, 2, 1), pm, cfg=cfg)
    assert before.method == "none" and "not VAT-registered" in before.render()
    assert "CHF 10'000" in before.render_bezugsteuer()
    after = vat.convert(Decimal("8.10"), "CHF", Date(2026, 3, 1), pm, cfg=cfg)
    assert after.method == "effective" and "InputVAT" in after.render()


# ── invoicing when not registered ────────────────────────────────────────────

_ISSUER = Issuer(name="Jane Doe", address=["Gasse 1", "3000 Bern"], vat_id="CHE-267.359.056")


def _invoice() -> Invoice:
    return Invoice(
        number="INV2026001",
        kind="domestic",
        currency="CHF",
        issue_date=Date(2026, 7, 2),
        supply=SupplyPeriod.month(2026, 7),
        customer=Party(name="Acme AG", address=["Bahnhofstrasse 1", "8001 Zürich"]),
        items=[LineItem(description="Consulting", quantity=Decimal(1), unit_price=Decimal(1000))],
    )


def test_an_unregistered_issuer_charges_no_vat() -> None:
    totals = compute(_invoice(), vat_registered=False)
    assert totals.vat_amount == 0 and totals.grand_total == Decimal("1000.00")
    assert not totals.vat_registered


def test_the_qr_bill_carries_no_vat_details_when_unregistered() -> None:
    inv = _invoice()
    billing = swico.billing_information(inv, _ISSUER, compute(inv, vat_registered=False))
    assert billing is not None and "/30/" not in billing and "/32/" not in billing


def test_the_vat_number_must_match_the_registration() -> None:
    render.check_vat_identity(_ISSUER, vat_registered=False)  # a bare UID is fine
    with pytest.raises(ValueError, match=r"Art\. 27"):
        render.check_vat_identity(
            _ISSUER.model_copy(update={"vat_id": "CHE-267.359.056 MWST"}), vat_registered=False
        )
    with pytest.raises(ValueError, match=r"Art\. 26"):
        render.check_vat_identity(_ISSUER.model_copy(update={"vat_id": None}), vat_registered=True)
    render.check_vat_identity(_ISSUER.model_copy(update={"vat_id": None}), vat_registered=False)


# ── the whole flow, as a user runs it ────────────────────────────────────────


def test_an_unregistered_freelancer_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = tmp_path / "jane-books"
    res = runner.invoke(
        app,
        [
            "init",
            str(proj),
            "--name",
            "Jane Doe",
            "--legal-form",
            "einzelfirma",
            "--vat-registered-since",
            "no",
            "--samples",
            "--yes",
            "--no-git",
        ],
    )
    assert res.exit_code == 0, res.output
    toml = (proj / "quints.toml").read_text()
    assert "vat_registered = false" in toml and "vat_method =" not in toml
    assert "not VAT-registered" in (proj / "AGENTS.md").read_text()
    books = (proj / "books" / "2026.bean").read_text()
    assert "OutputVAT" not in books.split("sample activity")[1]

    monkeypatch.chdir(proj)
    assert runner.invoke(app, ["check"]).exit_code == 0
    out = runner.invoke(app, ["invoice", "invoicing/acme-2026-07.yaml", "--json"])
    assert out.exit_code == 0, out.output
    payload = json.loads(out.output)
    assert payload["totals"]["vat_amount"] == "0" and payload["cross_check"]["ok"]
    report = runner.invoke(app, ["vat", "report", "-p", "2026-Q3"])
    assert report.exit_code == 1 and "not VAT-registered" in report.output
    check = runner.invoke(app, ["close", "check", "--year", "2026", "--json"])
    items = {i["id"]: i for i in json.loads(check.output)["items"]}
    assert items["vat"]["status"] == "pass"


def test_init_rejects_a_method_without_registration() -> None:
    with pytest.raises(init.InitError, match="not VAT-registered"):
        init.plan(init.Answers(vat_registered=False, vat_method="saldo", saldo_rates=("6.2",)))


def test_init_records_a_late_registration(tmp_path: Path) -> None:
    files = {
        str(f.path): f.content
        for f in init.plan(init.Answers(vat_registered_since=Date(2026, 4, 1)))
    }
    assert "vat_registered_since = 2026-04-01" in files["quints.toml"]


# ── the threshold ────────────────────────────────────────────────────────────

_TRADE = """
2020-01-01 open Assets:CH:GmbH:Receivable:Trade
2020-01-01 open Income:CH:GmbH:Consulting:External:Domestic
2020-01-01 open Income:CH:GmbH:Consulting:External:Export
2020-01-01 open Income:CH:GmbH:Rent:Exempt
2020-01-01 open Equity:CH:GmbH:Capital:Share

2025-03-01 * "Founders" "capital"
  Assets:CH:GmbH:Receivable:Trade                 20000.00 CHF
  Equity:CH:GmbH:Capital:Share                   -20000.00 CHF

2025-06-01 * "Kunde" "domestic"
  Assets:CH:GmbH:Receivable:Trade                 60000.00 CHF
  Income:CH:GmbH:Consulting:External:Domestic    -60000.00 CHF

2025-09-01 * "Abroad" "export — counts: the threshold is worldwide"
  Assets:CH:GmbH:Receivable:Trade                 25000.00 CHF
  Income:CH:GmbH:Consulting:External:Export      -25000.00 CHF

2025-10-01 * "Tenant" "exempt (Art. 21) — does not count"
  Assets:CH:GmbH:Receivable:Trade                 50000.00 CHF
  Income:CH:GmbH:Rent:Exempt                     -50000.00 CHF
"""


def _trade(tmp_path: Path) -> Path:
    path = tmp_path / "main.bean"
    path.write_text(_TRADE)
    return path


def test_months_active_matches_the_estv_example() -> None:
    assert liability.months_active(Date(2017, 3, 1), Date(2017, 12, 31)) == 10
    assert liability.months_active(Date(2026, 1, 1), Date(2026, 12, 31)) == 12


def test_a_partial_first_year_is_converted_to_a_full_one(tmp_path: Path) -> None:
    cfg = config.Config(vat_registered=False)
    result = liability.compute(_trade(tmp_path), Date(2025, 12, 31), cfg)
    (year,) = result.years
    # 85'000 over ten months → 102'000 for a full year: MWST-Info 02 Ziff. 5.3.
    assert year.turnover == Decimal("85000.00") and year.annualised == Decimal("102000.00")
    assert result.status == "must_register"
    assert (result.liable_from, result.register_by) == ("2026-01-01", "2026-01-31")


def test_a_running_year_is_only_on_course(tmp_path: Path) -> None:
    cfg = config.Config(vat_registered=False)
    result = liability.compute(_trade(tmp_path), Date(2025, 10, 31), cfg)
    assert not result.years[0].complete and result.status == "on_course"


def test_a_registered_business_below_the_threshold_may_deregister(tmp_path: Path) -> None:
    cfg = config.Config(vat_registered_since=Date(2020, 1, 1))
    result = liability.compute(_trade(tmp_path), Date(2026, 12, 31), cfg)
    assert [y.year for y in result.years] == [2025, 2026]
    assert result.status == "may_deregister"
    assert any("2027-03-01" in a for a in result.advice)


def test_cli_vat_liability_json(tmp_path: Path) -> None:
    toml = tmp_path / "quints.toml"
    toml.write_text("[entity]\nvat_registered = false\n")
    res = runner.invoke(
        app,
        [
            "--config",
            str(toml),
            "vat",
            "liability",
            "--at",
            "2025-12-31",
            "--json",
            "-f",
            str(_trade(tmp_path)),
        ],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["status"] == "must_register" and payload["years"][0]["annualised"] == "102000.00"


def test_the_final_return_is_due_60_days_after_liability_ends(tmp_path: Path) -> None:
    cfg = config.Config(
        vat_registered_since=Date(2025, 1, 1), vat_registered_until=Date(2026, 5, 31)
    )
    books = _ledger(tmp_path)
    report = mwst.compute(books, "2026-04-01", "2026-06-30", cfg=cfg)
    s = settlement.build_settlement(books, report, "2026-Q2", cfg=cfg)
    assert (s.settle_date, s.due) == ("2026-05-31", "2026-07-30")


def test_a_mid_year_registration_scaffolds_and_files_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = tmp_path / "late-books"
    res = runner.invoke(
        app,
        [
            "init",
            str(proj),
            "--legal-form",
            "gmbh",
            "--vat-registered-since",
            "2026-04-01",
            "--vat-method",
            "effective",
            "--samples",
            "--yes",
            "--no-git",
        ],
    )
    assert res.exit_code == 0, res.output
    # The books start in January; only VAT liability starts in April.
    assert "2026-01-01 open" in (proj / "accounts.bean").read_text()
    monkeypatch.chdir(proj)
    assert runner.invoke(app, ["check"]).exit_code == 0
    q2 = json.loads(runner.invoke(app, ["vat", "report", "-p", "2026-Q2", "--json"]).output)
    assert q2["liable_from"] == "2026-04-01"
    assert any("Einlageentsteuerung" in n for n in q2["notices"])
    q1 = runner.invoke(app, ["vat", "report", "-p", "2026-Q1"])
    assert q1.exit_code == 1 and "not VAT-registered" in q1.output
    check = runner.invoke(app, ["close", "check", "--year", "2026", "--json"])
    items = {i["id"]: i for i in json.loads(check.output)["items"]}
    assert items["vat"]["data"]["periods"] == ["VAT-2026-Q2", "VAT-2026-Q3", "VAT-2026-Q4"]
    # The sample July invoice falls after registration, so it charges VAT.
    inv = json.loads(
        runner.invoke(app, ["invoice", "invoicing/acme-2026-07.yaml", "--json"]).output
    )
    assert inv["totals"]["vat_amount"] == "81.00" and inv["cross_check"]["ok"]
