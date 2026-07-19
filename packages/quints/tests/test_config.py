"""Tests for quints.toml config loading and entity extraction (plan 5.1)."""

from dataclasses import replace
from datetime import date as Date
from decimal import Decimal
from pathlib import Path

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
income_prefix = "Income:XX:AG"
entity_marker = ":XX:AG:"

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
    assert cfg.operating_currency == "CHF"


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
