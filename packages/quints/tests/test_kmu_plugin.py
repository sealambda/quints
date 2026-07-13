"""Tests for the kmu: metadata plugin."""

from beancount import loader

_HEADER = 'plugin "quints.plugins.kmu"\n'


def _errors(ledger: str):
    _, errors, _ = loader.load_string(_HEADER + ledger)
    return errors


def test_ch_account_without_kmu_errors():
    errors = _errors("2024-01-01 open Expenses:CH:GmbH:IT:Hosting\n")
    assert len(errors) == 1
    assert "no kmu: code" in errors[0].message


def test_ch_account_with_kmu_passes():
    errors = _errors('2024-01-01 open Expenses:CH:GmbH:IT:Hosting\n  kmu: "6570"\n')
    assert errors == []


def test_malformed_kmu_code_errors():
    errors = _errors('2024-01-01 open Expenses:CH:GmbH:IT:Hosting\n  kmu: "657"\n')
    assert len(errors) == 1
    assert "invalid kmu:" in errors[0].message


def test_non_string_kmu_code_errors():
    errors = _errors("2024-01-01 open Expenses:CH:GmbH:IT:Hosting\n  kmu: 6570\n")
    assert len(errors) == 1
    assert "invalid kmu:" in errors[0].message


def test_us_accounts_exempt():
    errors = _errors("2024-01-01 open Expenses:US:LLC:Ops:Software USD\n")
    assert errors == []
