"""Every quints command shown in the docs must actually run.

The docs site (docs/**/*.md) and the README promise commands; this extracts
them from the fenced code blocks and runs each page as a session — a fresh
copy of the example project, commands executed in document order through the
CLI (no --config/--file overrides — the same resolution a copy-paste gets),
asserting a clean exit. If a documented command breaks — a renamed flag, a
changed default — this fails, so the docs can't silently rot.

Extraction rules (documented here because docs authors rely on them):

- Only ``bash`` and ``console`` fences are scanned. In console blocks just
  the ``$ ``-prefixed lines are commands (the rest is output); in bash
  blocks every non-comment line is considered.
- Only ``quints …`` and ``cd …`` lines execute. Anything else (uv, git,
  pipx, …) is skipped — this suite runs offline and in-process.
- A line containing a ``<placeholder>`` is skipped: it documents a pattern,
  not a runnable invocation. Prefer concrete commands where possible.
- An HTML comment containing ``no-test`` on the line above a fence skips the
  whole block (network commands, credential-dependent fetches).
- ``cd`` into a directory that doesn't exist (e.g. after a skipped
  ``git clone``) is skipped too.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quints.cli import app

runner = CliRunner()
REPO = Path(__file__).resolve().parents[3]
EXAMPLES = Path(__file__).resolve().parents[1] / "examples"

DOC_PAGES = sorted(p.relative_to(REPO) for p in (REPO / "docs").rglob("*.md"))
# The generated AGENTS.md is a doc page too — the one an agent actually reads.
# Its fenced commands run against the example project like every other page.
AGENTS_MD = EXAMPLES.relative_to(REPO) / "AGENTS.md"
PAGES = [Path("README.md"), AGENTS_MD, *DOC_PAGES]

_FENCE = re.compile(r"^```(\w*)")
_NO_TEST = re.compile(r"<!--.*no-test.*-->")
_PLACEHOLDER = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class _Command:
    line: str  # verbatim doc line, for failure messages
    argv: list[str]


def _commands(text: str) -> list[_Command]:
    """The quints/cd command sequence a reader would run for one page."""
    commands: list[_Command] = []
    lang: str | None = None  # inside a fence when not None
    skip_block = False
    prev = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        fence = _FENCE.match(line)
        if fence and lang is None:
            lang = fence.group(1)
            skip_block = bool(_NO_TEST.search(prev))
        elif line.startswith("```"):
            lang = None
        elif lang in ("bash", "console") and not skip_block:
            cmd = line.strip()
            if lang == "console":
                if not cmd.startswith("$ "):
                    continue  # output line
                cmd = cmd[2:]
            elif cmd.startswith("$ "):
                cmd = cmd[2:]
            if _PLACEHOLDER.search(cmd):
                continue
            argv = shlex.split(cmd, comments=True)
            if argv and argv[0] in ("quints", "cd"):
                commands.append(_Command(line=cmd, argv=argv))
        prev = line
    return commands


@pytest.mark.parametrize("page", PAGES, ids=str)
def test_documented_commands_run(
    page: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands = _commands((REPO / page).read_text())
    proj = tmp_path / "example"
    shutil.copytree(EXAMPLES, proj)
    monkeypatch.chdir(proj)  # restores the original cwd on teardown
    for cmd in commands:
        if cmd.argv[0] == "cd":
            if Path(cmd.argv[1]).is_dir():
                os.chdir(cmd.argv[1])
            continue
        result = runner.invoke(app, cmd.argv[1:])
        assert result.exit_code == 0, (
            f"{page}: `{cmd.line}` exited {result.exit_code}:\n{result.output}"
        )


def test_extractor_sees_the_docs() -> None:
    # If the docs move or the extractor breaks, the suite above would pass
    # vacuously. Pin a floor: the site exists and yields real commands.
    assert len(DOC_PAGES) >= 8, f"docs/ pages missing: found only {DOC_PAGES}"
    total = sum(len(_commands((REPO / p).read_text())) for p in PAGES)
    assert total >= 25, f"only {total} documented commands extracted — extractor broken?"
    agents = len(_commands((REPO / AGENTS_MD).read_text()))
    assert agents >= 5, f"AGENTS.md yields only {agents} runnable commands"


def test_hosted_schemas_are_in_sync() -> None:
    # The scaffold's yaml-language-server modelines point at
    # <site>/schema/<name>.schema.json; the committed docs/schema/ files that
    # publish there must match the Pydantic models. Regenerate: `make docs`.
    import json

    from quints.invoice import model as m

    for name, mdl in (
        ("invoice", m.Invoice),
        ("issuer", m.Issuer),
        ("customers", m.CustomerRegistry),
    ):
        path = REPO / "docs" / "schema" / f"{name}.schema.json"
        assert path.exists(), f"{path} missing — run `make docs`"
        assert json.loads(path.read_text()) == mdl.model_json_schema(), (
            f"docs/schema/{name}.schema.json is stale — run `make docs`"
        )
