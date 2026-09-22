"""Tests for quints.toml config loading and entity extraction (plan 5.1)."""

from dataclasses import replace
from datetime import date as Date
from decimal import Decimal
from pathlib import Path

import pytest

from quints import config, mwst

_TOML = """
[entity]
name = "Musterfirma AG"
vat_registered_since = 2025-06-01

[ledger]
main = "books.bean"

[accounts]
input_vat = "Assets:XX:AG:VAT:In"
output_vat = "Liabilities:XX:AG:VAT:Out"
bezugsteuer = "Liabilities:XX:AG:VAT:Reverse"
payable_vat = "Liabilities:XX:AG:VAT:Due"
payable = "Liabilities:XX:AG:Creditors"
income_prefix = "Income:XX:AG"
entity_marker = ":XX:AG:"

[payables]
default_terms_days = 20

[report]
language = "de"
"""

# A stranger's ledger: different entity, different account names.
_STRANGER_LEDGER = """
2025-01-01 open Assets:XX:AG:Bank CHF
2025-01-01 open Assets:XX:AG:VAT:In CHF
2025-01-01 open Liabilities:XX:AG:VAT:Out CHF
2025-01-01 open Income:XX:AG:Consulting:Export
2025-01-01 open Income:XX:AG:Consulting:Domestic

2025-03-01 * "Kunde" "domestic invoice"
    Assets:XX:AG:Bank                          1081.00 CHF
    Income:XX:AG:Consulting:Domestic          -1000.00 CHF
    Liabilities:XX:AG:VAT:Out                   -81.00 CHF

2025-04-01 * "Ausland" "export"
    Assets:XX:AG:Bank                           500.00 CHF
    Income:XX:AG:Consulting:Export             -500.00 CHF
"""


def test_defaults_are_generic_swiss_gmbh() -> None:
    cfg = config.Config()
    assert cfg.entity_name == "Example GmbH"
    assert cfg.input_vat == "Assets:CH:GmbH:Tax:InputVAT"
    assert cfg.payable == "Liabilities:CH:GmbH:Payable:Trade"
    assert cfg.payables_default_terms_days == 30
    assert cfg.vat_registered_since is None
    # importers are opt-in: no [import.*] section, no importer
    assert cfg.import_ubs is None
    assert cfg.import_wise is None
    assert cfg.import_stripe is None


_IMPORT_TOML = """
[import.ubs]
iban = "CH9300762011623852957"
rules = [['\\bacme\\b', "Assets:CH:GmbH:Receivable:Trade", "*"]]

[import.wise]
holder = "Muster GmbH"

[import.wise.accounts]
EUR = "Assets:CH:GmbH:Current:Wise:EUR"

[import.stripe]
account_id = "acct_TEST123"
"""


def test_load_import_sections(tmp_path: Path) -> None:
    path = tmp_path / "quints.toml"
    path.write_text(_IMPORT_TOML)
    cfg = config.load(path)
    assert cfg.import_ubs is not None
    assert cfg.import_wise is not None
    assert cfg.import_stripe is not None
    assert cfg.import_ubs.iban == "CH9300762011623852957"
    assert cfg.import_ubs.account == "Assets:CH:GmbH:Current:UBS:CHF"  # default kept
    assert cfg.import_ubs.rules == ((r"\bacme\b", "Assets:CH:GmbH:Receivable:Trade", "*"),)
    assert cfg.import_wise.holder == "Muster GmbH"
    assert cfg.import_wise.account_map == {"EUR": "Assets:CH:GmbH:Current:Wise:EUR"}
    assert cfg.import_stripe.account_id == "acct_TEST123"
    assert cfg.import_stripe.rules == ()


def test_load_toml(tmp_path: Path) -> None:
    path = tmp_path / "quints.toml"
    path.write_text(_TOML)
    cfg = config.load(path)
    assert cfg.entity_name == "Musterfirma AG"
    assert cfg.vat_registered_since == Date(2025, 6, 1)
    assert cfg.ledger_main == Path("books.bean")
    assert cfg.input_vat == "Assets:XX:AG:VAT:In"
    assert cfg.report_language == "de"
    # unset keys keep their defaults
    assert cfg.export_marker == ":Export"
    assert cfg.export_goods_marker == ":ExportGoods"
    assert cfg.reduced_marker == ":Reduced"
    assert cfg.operating_currency == "CHF"
    assert cfg.prices_source == "beanprice_bazg"
    assert cfg.prices_currencies == ("USD", "EUR")


def test_load_prices_section(tmp_path: Path) -> None:
    path = tmp_path / "quints.toml"
    path.write_text(
        "[prices]\n"
        'source = "ecbrates"\n'
        'currencies = ["USD", "GBP", "SEK"]\n'
        'tickers = { USD = "EUR-USD", GBP = "EUR-GBP" }\n'
    )
    cfg = config.load(path)
    assert cfg.prices_source == "ecbrates"
    assert cfg.prices_currencies == ("USD", "GBP", "SEK")
    assert dict(cfg.prices_tickers) == {"USD": "EUR-USD", "GBP": "EUR-GBP"}


def test_stranger_entity_gets_correct_mwst(tmp_path: Path) -> None:
    """Plan 5.1 exit criterion: foreign chart of accounts + inline Config."""
    ledger_file = tmp_path / "books.bean"
    ledger_file.write_text(_STRANGER_LEDGER)
    cfg = config.Config(
        entity_name="Musterfirma AG",
        input_vat="Assets:XX:AG:VAT:In",
        output_vat="Liabilities:XX:AG:VAT:Out",
        bezugsteuer="Liabilities:XX:AG:VAT:Reverse",
        payable_vat="Liabilities:XX:AG:VAT:Due",
        income_prefix="Income:XX:AG",
        entity_marker=":XX:AG:",
    )
    report = mwst.compute(ledger_file, "2025-01-01", "2025-06-30", cfg=cfg)
    assert report.z299 == Decimal("1000.00")  # domestic net
    assert report.z221 == Decimal("500.00")  # export
    assert report.z303_tax == Decimal("81.00")
    assert report.z500 == Decimal("81.00")


def test_vat_registered_since_clamps_period(tmp_path: Path) -> None:
    ledger_file = tmp_path / "books.bean"
    ledger_file.write_text(_STRANGER_LEDGER)
    base = config.Config(
        input_vat="Assets:XX:AG:VAT:In",
        output_vat="Liabilities:XX:AG:VAT:Out",
        income_prefix="Income:XX:AG",
    )
    unclamped = mwst.compute(ledger_file, "2025-01-01", "2025-06-30", cfg=base)
    clamped = mwst.compute(
        ledger_file,
        "2025-01-01",
        "2025-06-30",
        cfg=replace(base, vat_registered_since=Date(2025, 3, 15)),
    )
    assert unclamped.z303_tax == Decimal("81.00")
    assert clamped.z303_tax == Decimal("0")  # March invoice predates liability
    assert clamped.z221 == Decimal("500.00")  # April export still in


_MARKERS_TOML = """
[accounts]
income_prefix = "Income:CH:GmbH"
export_marker = ":Abroad"
export_goods_marker = ":Ausfuhr"
exempt_marker = ":Ausgenommen"
optioned_marker = ":Optiert"
reduced_marker = ":Reduziert"
lodging_marker = ":Hotel"
"""

_MARKED_LEDGER = """
2026-01-01 open Assets:CH:GmbH:Current:UBS:CHF CHF
2026-01-01 open Liabilities:CH:GmbH:Tax:OutputVAT CHF
2026-01-01 open Income:CH:GmbH:Services:Abroad CHF
2026-01-01 open Income:CH:GmbH:Goods:Ausfuhr CHF
2026-01-01 open Income:CH:GmbH:Kurse:Ausgenommen CHF
2026-01-01 open Income:CH:GmbH:Buecher:Reduziert CHF

2026-07-02 * "Abroad" "Service abroad"
    Assets:CH:GmbH:Current:UBS:CHF          100.00 CHF
    Income:CH:GmbH:Services:Abroad         -100.00 CHF

2026-07-03 * "Overseas" "Goods exported"
    Assets:CH:GmbH:Current:UBS:CHF          200.00 CHF
    Income:CH:GmbH:Goods:Ausfuhr           -200.00 CHF

2026-07-04 * "Schule" "Ausgenommene Leistung"
    Assets:CH:GmbH:Current:UBS:CHF          300.00 CHF
    Income:CH:GmbH:Kurse:Ausgenommen       -300.00 CHF

2026-07-05 * "Buchhandlung" "Buecher"
    Assets:CH:GmbH:Current:UBS:CHF          410.40 CHF
    Income:CH:GmbH:Buecher:Reduziert       -400.00 CHF
    Liabilities:CH:GmbH:Tax:OutputVAT       -10.40 CHF
"""


def test_marker_keys_are_configurable(tmp_path: Path) -> None:
    """The Ziffer markers are entity config, like every other account name."""
    path = tmp_path / "quints.toml"
    path.write_text(_MARKERS_TOML)
    cfg = config.load(path)
    assert cfg.export_marker == ":Abroad"
    assert cfg.export_goods_marker == ":Ausfuhr"
    assert cfg.exempt_marker == ":Ausgenommen"
    assert cfg.optioned_marker == ":Optiert"
    assert (cfg.reduced_marker, cfg.lodging_marker) == (":Reduziert", ":Hotel")

    ledger_file = tmp_path / "books.bean"
    ledger_file.write_text(_MARKED_LEDGER)
    report = mwst.compute(ledger_file, "2026-07-01", "2026-09-30", cfg=cfg)
    assert report.z221 == Decimal("100.00")
    assert report.z220 == Decimal("200.00")
    assert report.z230 == Decimal("300.00")
    assert report.z313_net == Decimal("400.00") and report.z313_tax == Decimal("10.40")
    assert report.violations == []


def test_annual_filing_is_open_to_the_effective_method(tmp_path: Path) -> None:
    # Art. 35a MWSTG (since 2025) lets either method settle once a year on
    # request; `quints init` only writes [vat] for saldo, so this is the
    # hand-written case and it has to validate.
    path = tmp_path / "quints.toml"
    path.write_text('[entity]\nvat_method = "effective"\n\n[vat]\nperiod = "year"\n')
    cfg = config.load(path)
    assert cfg.vat_method == "effective" and cfg.period_kind == "year"
    assert cfg.saldo == ()

    path.write_text('[vat]\nperiod = "month"\n')
    with pytest.raises(config.ConfigError, match="quarter, half-year, year"):
        config.load(path)
