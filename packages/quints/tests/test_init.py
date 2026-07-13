"""Tests for `quints init` — the deterministic project scaffolder.

The keystone check is `test_generated_ledger_reports_expected_numbers`: it
scaffolds a project with the sample quarter and runs the reporting commands
against it, asserting known figures. That is the end-to-end smoke test the CI
`pytest` job exercises on every push.
"""

import json
import re
from datetime import date as Date
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quints import config, init, kmu, ledger, mwst, receivables
from quints.cli import app

runner = CliRunner()

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _example_answers() -> init.Answers:
    return init.load_answers(EXAMPLES / "answers.toml")


def _files(answers: init.Answers) -> dict[str, str]:
    return {f.path.name: f.content for f in init.plan(answers)}


def test_plan_is_deterministic():
    answers = init.Answers(include_samples=True, importers=("ubs", "wise"))
    assert init.plan(answers) == init.plan(answers)


def test_backbone_accounts_carry_known_kmu_codes():
    # Every account the backbone opens must map to a real KMU Kontenrahmen
    # code, or `quints.plugins.kmu` rejects the ledger.
    main = _files(init.Answers(importers=("ubs", "wise", "stripe")))["main.bean"]
    codes = re.findall(r'kmu: "(\d{4})"', main)
    assert codes, "backbone emitted no accounts"
    unknown = [c for c in codes if c not in kmu.KMU_NAMES]
    assert not unknown, f"kmu codes absent from KMU_NAMES: {unknown}"


def test_generated_quints_toml_loads_back_through_config(tmp_path: Path):
    # The emitted quints.toml must parse with the production config loader.
    init.write(tmp_path, init.plan(init.Answers(entity_name="Round GmbH", importers=("wise",))))
    cfg = config.load(tmp_path / "quints.toml")
    assert cfg.entity_name == "Round GmbH"
    assert cfg.import_wise is not None
    assert cfg.import_wise.account_map["EUR"].endswith("Wise:EUR")


def test_write_refuses_overwrite_without_force(tmp_path: Path):
    files = init.plan(init.Answers())
    first = init.write(tmp_path, files)
    assert first.written and not first.skipped
    second = init.write(tmp_path, files)
    assert not second.written and second.skipped
    forced = init.write(tmp_path, files, force=True)
    assert forced.written and not forced.skipped


def test_rejects_saldo_method():
    with pytest.raises(init.InitError):
        init.plan(init.Answers(vat_method="saldo"))


def test_rejects_unknown_importer():
    with pytest.raises(init.InitError):
        init.plan(init.Answers(importers=("paypal",)))


def test_examples_project_is_in_sync():
    # The committed examples/ is regenerated, never hand-edited:
    # `quints init --answers examples/answers.toml` must reproduce it exactly.
    for f in init.plan(_example_answers()):
        if not f.content:  # directory-marker .gitkeep files
            continue
        committed = EXAMPLES / f.path
        assert committed.read_text() == f.content, (
            f"{f.path} is stale — regenerate examples/ from answers.toml"
        )


def test_generated_ledger_reports_expected_numbers(tmp_path: Path):
    # Acceptance smoke test — scaffold with samples, then every headline report
    # must agree on known figures. See test_mwst for the same conventions.
    answers = init.Answers(entity_name="Smoke GmbH", importers=("wise",), include_samples=True)
    init.write(tmp_path, init.plan(answers))
    main = tmp_path / "main.bean"

    _entries, errors = ledger.load_entries(main)
    assert not errors, "bean-check / kmu plugin found errors in the generated ledger"

    cfg = config.load(tmp_path / "quints.toml")

    report = mwst.compute(main, "2026-07-01", "2026-09-30", cfg)
    assert report.z303_tax == Decimal("81.00")  # 8.1% output VAT on 1000 CHF
    assert report.z382_tax == Decimal("7.53")  # Bezugsteuer (reverse charge)
    assert report.z299 == Decimal("1000.00")  # domestic turnover
    assert report.z221 == Decimal("470.00")  # export: 500 EUR @ 0.94

    erfolg = kmu.compute_erfolg(main, "2026-01-01", "2026-12-31", cfg)
    assert erfolg.ebit == Decimal("1376.00")  # revenue 1470 − IT expense 94
    assert erfolg.result == Decimal("1376.00")

    open_inv, _at = receivables.compute(main, Date(2026, 12, 31), cfg)
    assert [o.number for o in open_inv] == ["INV2026015"]
    assert open_inv[0].open_amount == Decimal("500.00")
    assert open_inv[0].currency == "EUR"


def test_cli_init_answers_file_and_force(tmp_path: Path):
    # Guards the CLI wiring: --answers is non-interactive, --json is stable,
    # and a second run without --force skips rather than clobbers.
    answers = tmp_path / "answers.toml"
    answers.write_text('entity_name = "CLI GmbH"\nimporters = ["ubs"]\n')
    target = tmp_path / "proj"

    res = runner.invoke(app, ["init", str(target), "--answers", str(answers), "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["entity"] == "CLI GmbH"
    assert any(p.endswith("main.bean") for p in payload["written"])
    assert (target / "quints.toml").exists()

    again = runner.invoke(app, ["init", str(target), "--answers", str(answers), "--json"])
    assert json.loads(again.output)["written"] == []  # nothing overwritten without --force
