"""quints.toml is checked, not skimmed: a typo is an error, never a default.

`vat_registered = "no"` used to count as registered (a non-empty string),
`vat_methd = "saldo"` and `input_vatt` were ignored, and a ledger outside the
current directory silently ran on the built-in Example GmbH config.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from quints import config
from quints.cli import app

runner = CliRunner()
EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _load(tmp_path: Path, text: str) -> config.Config:
    path = tmp_path / "quints.toml"
    path.write_text(text)
    return config.load(path)


@pytest.mark.parametrize(
    ("toml", "message"),
    [
        ('[entity]\nvat_methd = "saldo"\n', "vat_methd.*did you mean vat_method"),
        ('[accounts]\ninput_vatt = "Assets:X"\n', "input_vatt.*did you mean input_vat"),
        ('[entitiy]\nname = "X AG"\n', r"\[entitiy\].*did you mean \[entity\]"),
        ('[entity]\nvat_registered = "no"\n', "vat_registered.*true or false"),
        ("[payables]\ndefault_terms_days = true\n", "default_terms_days.*whole number"),
        ("[entity]\nvat_registered_since = 2026\n", "vat_registered_since.*date"),
        ('[import.ubs]\niban = "CH93"\nrulez = []\n', r"\[import\.ubs\] rulez.*rules"),
        ('[import.ubs]\nrules = [["acme", "Assets:X"]]\n', "rules.*payee regex, account, flag"),
        ("[[vat.saldo]]\nrate = 6.2\nlabl = 'x'\n", r"\[\[vat\.saldo\]\] labl.*label"),
        ('[prices]\ncurrencies = "EUR"\n', "currencies.*list"),
        ('export_marker = ":X"\n', r"export_marker belongs under \[accounts\]"),
    ],
)
def test_typos_and_wrong_types_are_errors(tmp_path: Path, toml: str, message: str) -> None:
    with pytest.raises(config.ConfigError, match=message):
        _load(tmp_path, toml)


def test_every_problem_is_listed_at_once(tmp_path: Path) -> None:
    with pytest.raises(config.ConfigError) as err:
        _load(tmp_path, '[entity]\nvat_methd = "saldo"\nvat_registered = "no"\n')
    assert "vat_methd" in str(err.value) and "vat_registered" in str(err.value)


def test_the_shipped_configs_still_load(tmp_path: Path) -> None:
    assert config.load(EXAMPLES / "quints.toml").entity_name


def test_cli_refuses_a_mistyped_config(tmp_path: Path) -> None:
    toml = tmp_path / "quints.toml"
    toml.write_text('[entity]\nvat_registered = "no"\n')
    led = tmp_path / "main.bean"
    led.write_text("")
    res = runner.invoke(app, ["--config", str(toml), "vat", "report", "-p", "2026", "-f", str(led)])
    assert res.exit_code == 1 and "vat_registered" in res.output


def test_config_is_found_next_to_the_ledger(tmp_path: Path) -> None:
    """`quints -f ~/books/main.bean …` from elsewhere uses ~/books/quints.toml
    (or the nearest one above it), not the built-in Example GmbH."""
    books = tmp_path / "books"
    (books / "sub").mkdir(parents=True)
    (books / "quints.toml").write_text('[entity]\nname = "Lookup AG"\n')
    for led in (books / "main.bean", books / "sub" / "main.bean"):
        led.write_text("")
        res = runner.invoke(app, ["vat", "report", "-p", "2026-Q1", "-f", str(led)])
        assert res.exit_code == 0, res.output
        assert "Lookup AG" in res.output
    # --config still wins.
    other = tmp_path / "other.toml"
    other.write_text('[entity]\nname = "Explicit GmbH"\n')
    argv = [
        "--config",
        str(other),
        "vat",
        "report",
        "-p",
        "2026-Q1",
        "-f",
        str(books / "main.bean"),
    ]
    assert "Explicit GmbH" in runner.invoke(app, argv).output
