"""Every command shown in the README must actually run.

The README tells a newcomer to `cd packages/quints/examples` and run these
commands. This copies that example to a scratch dir, changes into it, and runs
each *literal* command through the CLI (no `--config`/`--file` overrides — the
same resolution a copy-paste gets), asserting a clean exit. If a documented
command breaks — a renamed flag, a changed default — this fails, so the docs
can't silently rot. `prices sync` is excluded by design (it needs network);
`init` is covered by test_init.
"""

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quints.cli import app

runner = CliRunner()
EXAMPLES = Path(__file__).resolve().parents[1] / "examples"

# Exactly the runnable commands in README.md's "by the job" sections.
README_COMMANDS: list[list[str]] = [
    ["check"],
    ["mwst", "-q", "2026-Q3"],
    ["status"],
    ["receivables"],
    ["report", "bilanz", "--at", "2026-12-31"],
    ["report", "erfolg", "--year", "2026"],
    ["report", "statements", "--year", "2026", "--lang", "de"],
    ["import", "ubs", "statements/ubs-2026.mt940"],
    ["invoice", "invoicing/acme-2026-07.yaml"],
    ["fx", "revalue", "--at", "2026-12-31"],
]


@pytest.mark.parametrize("cmd", README_COMMANDS, ids=lambda c: " ".join(c))
def test_readme_command_runs(
    cmd: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = tmp_path / "example"
    shutil.copytree(EXAMPLES, proj)
    monkeypatch.chdir(proj)
    result = runner.invoke(app, cmd)
    assert result.exit_code == 0, (
        f"`quints {' '.join(cmd)}` exited {result.exit_code}:\n{result.output}"
    )
