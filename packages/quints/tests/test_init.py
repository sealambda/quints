"""Tests for `quints init` — the deterministic project scaffolder.

The keystone check is `test_generated_ledger_reports_expected_numbers`: it
scaffolds a project with the sample quarter and runs the reporting commands
against it, asserting known figures. That is the end-to-end smoke test the CI
`pytest` job exercises on every push.
"""

import json
import re
import subprocess
import sys
from datetime import date as Date

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib
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
    for legal_form in ("gmbh", "ag", "einzelfirma"):
        chart = _files(init.Answers(legal_form=legal_form, importers=("ubs", "wise", "stripe")))[
            "accounts.bean"
        ]
        codes = re.findall(r'kmu: "(\d{4})"', chart)
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


def test_rejects_unknown_legal_form():
    with pytest.raises(init.InitError, match="Personengesellschaft"):
        init.plan(init.Answers(legal_form="personengesellschaft"))


def test_einzelfirma_gets_the_official_klasse_28_variant():
    # The veb.ch KMU Kontenrahmen prints a distinct equity block for
    # Einzelunternehmen: no share capital, but 2820 contributions and
    # 2850 Privat. The namespace marker follows the legal form.
    files = _files(init.Answers(legal_form="einzelfirma", include_samples=True))
    chart = files["accounts.bean"]
    assert ":CH:Einzelfirma:" in chart and "GmbH" not in chart
    assert "Capital:Share" not in chart
    assert 'kmu: "2820"' in chart and 'kmu: "2850"' in chart
    assert "Eigenkapital" not in chart  # comments render the English names
    # The sample opening entry books an owner contribution, not share capital.
    books = files["2026.bean"]
    assert "Equity:CH:Einzelfirma:Contributions" in books
    toml = files["quints.toml"]
    assert 'legal_form = "einzelfirma"' in toml
    assert 'entity_marker = ":CH:Einzelfirma:"' in toml


def test_ag_marker_and_labels():
    files = _files(init.Answers(legal_form="ag", entity_name="Example AG"))
    assert ":CH:AG:" in files["accounts.bean"]
    assert "Equity:CH:AG:Capital:Share" in files["accounts.bean"]
    assert kmu.kmu_name("2800", "de", "ag") == "Aktienkapital"
    assert kmu.label("share_capital", "de", "einzelfirma") == "Eigenkapital"
    assert kmu.kmu_name("2800", "de") == "Stammkapital"  # GmbH stays the default


def test_generated_pyproject_declares_quints(tmp_path: Path):
    files = _files(init.Answers(entity_name="Round GmbH"))
    raw = tomllib.loads(files["pyproject.toml"])
    assert raw["project"]["name"] == "round-gmbh"
    assert "quints" in raw["project"]["dependencies"]
    assert raw["tool"]["uv"]["package"] is False


def test_einzelfirma_ledger_loads_and_reports(tmp_path: Path):
    # The einzelfirma scaffold must be as runnable as the GmbH one: kmu plugin
    # clean, and the sample quarter reports the same EBIT.
    answers = init.Answers(entity_name="Jane Doe", legal_form="einzelfirma", include_samples=True)
    init.write(tmp_path, init.plan(answers))
    main = tmp_path / "main.bean"
    _entries, errors = ledger.load_entries(main)
    assert not errors, errors
    cfg = config.load(tmp_path / "quints.toml")
    assert cfg.legal_form == "einzelfirma"
    assert cfg.entity_marker == ":CH:Einzelfirma:"
    erfolg = kmu.compute_erfolg(main, "2026-01-01", "2026-12-31", cfg)
    assert erfolg.result == Decimal("1376.00")
    bilanz = kmu.compute_bilanz(main, "2026-12-31", cfg)
    assert bilanz.legal_form == "einzelfirma"
    assert bilanz.total_assets == bilanz.total_liabilities_equity


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


def test_sample_invoices_render_and_reconcile(tmp_path: Path):
    # --samples must make the invoice generator testable out of the box: a
    # domestic QR-bill and a reverse-charge export, both reconciling against
    # the sample quarter's ^INV… bookings.
    from quints.invoice import model as im
    from quints.invoice import render as ir
    from quints.invoice import verify as iv

    answers = init.Answers(entity_name="Smoke GmbH", include_samples=True)
    init.write(tmp_path, init.plan(answers))
    main = tmp_path / "main.bean"
    registry = im.load_customers(tmp_path / "invoicing/customers.yaml")
    issuer = im.load_issuer(tmp_path / "invoicing/issuer.yaml")
    assert issuer.name == "Smoke GmbH"

    domestic = im.load_invoice(tmp_path / "invoicing/acme-2026-07.yaml", registry)
    _path, totals, payload = ir.render(domestic, issuer, tmp_path / "acme.pdf")
    assert payload is not None and payload.splitlines()[0] == "SPC"  # QR part present
    cc = iv.cross_check(main, domestic, totals)
    assert cc.found and cc.ok and cc.date_ok

    export = im.load_invoice(tmp_path / "invoicing/globex-2026-08.yaml", registry)
    _path, totals, payload = ir.render(export, issuer, tmp_path / "globex.pdf")
    assert payload is None  # no QR-bill on a foreign invoice
    cc = iv.cross_check(main, export, totals)
    assert cc.found and cc.ok and cc.date_ok


def test_cli_scaffold_to_invoice_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # The exact steps a new user types, through the CLI: `quints init …
    # --samples`, then `quints invoice` on each sample invoice inside the
    # project. Guards the whole chain — invoicing/ lands on disk, quints.toml
    # resolves from the cwd, the QR-bill renders, the ledger cross-check
    # matches. Einzelfirma on purpose: its account namespace differs from the
    # built-in defaults, so a config-resolution regression can't hide.
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
            "--lang",
            "en",
            "--samples",
        ],
    )
    assert res.exit_code == 0, res.output
    for rel in (
        "invoicing/issuer.yaml",
        "invoicing/customers.yaml",
        "invoicing/acme-2026-07.yaml",
        "invoicing/globex-2026-08.yaml",
    ):
        assert (proj / rel).exists(), f"scaffold did not create {rel}:\n{res.output}"

    monkeypatch.chdir(proj)
    # Rendered invoices are filed per the beancount documents convention:
    # documents/<income account tree>/<date>.<customer>.<number>.pdf.
    income = proj / "documents/Income/CH/Einzelfirma/Consulting/External"
    for invoice_file, filed in (
        ("acme-2026-07", income / "Domestic/2026-07-02.acme-ag.INV2026014.pdf"),
        ("globex-2026-08", income / "Export/2026-08-05.globex-ltd.INV2026015.pdf"),
    ):
        res = runner.invoke(app, ["invoice", f"invoicing/{invoice_file}.yaml"])
        assert res.exit_code == 0, res.output
        assert "Ledger match" in res.output, res.output
        assert filed.exists(), res.output


def test_no_invoicing_files_without_samples():
    assert not any(f.path.parts[0] == "invoicing" for f in init.plan(init.Answers()))


def test_agent_payload_is_loaded_and_accurate():
    # CLAUDE.md @-includes AGENTS.md so Claude Code sessions start with the
    # instructions in context; the commands AGENTS.md quotes must be complete
    # (konten needs a period), and the sample checklist only appears when
    # there is sample data to replace.
    files = _files(init.Answers(include_samples=True, importers=("ubs", "wise")))
    assert files["CLAUDE.md"] == "@AGENTS.md\n"
    agents = files["AGENTS.md"]
    assert "quints report konten --year 2026" in agents
    assert "Sample data — replace before the books are real" in agents
    assert "QUINTS_WISE_API_TOKEN" in agents
    assert "quints import stripe" not in agents  # only the configured roster
    bare = _files(init.Answers())["AGENTS.md"]
    assert "Sample data" not in bare
    assert "quints.toml` (supported: ubs, wise, stripe)" in bare


def test_invoicing_yaml_carries_hosted_schema_modeline():
    files = _files(init.Answers(include_samples=True))
    for name, schema in [
        ("issuer.yaml", "issuer"),
        ("customers.yaml", "customers"),
        ("acme-2026-07.yaml", "invoice"),
        ("globex-2026-08.yaml", "invoice"),
    ]:
        first = files[name].splitlines()[0]
        assert first == (
            f"# yaml-language-server: $schema={config.DOCS_URL}/schema/{schema}.schema.json"
        ), f"{name} lacks the schema modeline: {first}"


def test_gitignore_keeps_documents_tree_committed():
    # A blanket *.pdf ignore would silently exclude documents/ — the filed
    # sources and rendered invoices the ledger links to must be committable.
    gitignore = _files(init.Answers())[".gitignore"]
    assert "*.pdf" not in gitignore
    assert "/staging/" in gitignore


def test_cli_init_commits_pristine_scaffold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # No git identity is configured in CI — provide one via the environment.
    for var in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(var, "quints test")
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(var, "test@example.invalid")
    proj = tmp_path / "books"
    res = runner.invoke(app, ["init", str(proj), "--yes", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["git"] == {"initialized": True, "committed": True, "detail": ""}
    assert (proj / ".git").is_dir()
    log = subprocess.run(  # noqa: S603 — fixed git argv in a test
        ["git", "-C", str(proj), "log", "--format=%s"],  # noqa: S607
        capture_output=True,
        text=True,
    )
    assert log.stdout.strip() == "Scaffold books with quints init"
    status = subprocess.run(  # noqa: S603 — fixed git argv in a test
        ["git", "-C", str(proj), "status", "--porcelain"],  # noqa: S607
        capture_output=True,
        text=True,
    )
    assert status.stdout == ""  # the initial commit captured the whole scaffold


def test_cli_init_never_nests_a_repo_inside_one(tmp_path: Path):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)  # noqa: S603, S607
    proj = tmp_path / "books"
    res = runner.invoke(app, ["init", str(proj), "--yes", "--json"])
    assert res.exit_code == 0, res.output
    git = json.loads(res.output)["git"]
    assert git["initialized"] is False and "already inside" in git["detail"]
    assert not (proj / ".git").exists()


def test_cli_init_no_git_opts_out(tmp_path: Path):
    proj = tmp_path / "books"
    res = runner.invoke(app, ["init", str(proj), "--yes", "--no-git", "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["git"] is None
    assert not (proj / ".git").exists()


def test_cli_init_without_tty_aborts_cleanly_before_writing(tmp_path: Path):
    # `init` prompts for unanswered questions; with no stdin (agent, script,
    # CI) the prompt aborts — that must happen BEFORE anything is written, so
    # a failed run never leaves a half-scaffolded project. Non-interactive
    # callers pass the full flag set, --yes, or --answers.
    proj = tmp_path / "books"
    res = runner.invoke(app, ["init", str(proj), "--samples"])
    assert res.exit_code != 0
    assert not proj.exists()


def test_cli_version_flag():
    # `quints --version` must report the installed distribution's version —
    # the first thing to check when a scaffold is missing newer files.
    from importlib.metadata import version

    res = runner.invoke(app, ["--version"])
    assert res.exit_code == 0
    assert res.output.strip() == version("quints")


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
