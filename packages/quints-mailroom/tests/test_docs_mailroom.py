"""Every example on the mailroom docs page must actually work.

docs/guides/mailroom.md carries two kinds of fences:

- ``bash`` blocks are the local demo. They are concatenated in page order
  and executed as one real shell script (the installed ``quints-mailroom``
  entry point, a scratch cwd) — the same copy-paste a reader would do. The
  documented outcomes (attachment in the books inbox, threaded reply in the
  outbox Maildir) are then asserted on the filesystem.
- ``toml`` blocks are complete mailroom.toml examples. Each must load
  through :func:`quints_mailroom.config.load`, so a renamed key or changed
  default breaks this test before it breaks a reader.

An HTML comment containing ``no-test`` on the line above a fence skips it
(network installs, credential-dependent commands) — same convention as
packages/quints/tests/test_docs.py.
"""

from __future__ import annotations

import mailbox
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from quints_mailroom import config as config_mod

REPO = Path(__file__).resolve().parents[3]
PAGE = REPO / "docs" / "guides" / "mailroom.md"

_FENCE = re.compile(r"^```(\w+)")
_NO_TEST = re.compile(r"<!--.*no-test.*-->")


def _blocks(lang: str) -> list[str]:
    """Fenced ``lang`` blocks from the page, in order, minus no-test ones."""
    blocks: list[str] = []
    current: list[str] | None = None
    prev = ""
    for raw in PAGE.read_text().splitlines():
        fence = _FENCE.match(raw)
        if current is None and fence:
            if fence.group(1) == lang and not _NO_TEST.search(prev):
                current = []
        elif current is not None and raw.startswith("```"):
            blocks.append("\n".join(current))
            current = None
        elif current is not None:
            current.append(raw)
        prev = raw
    return blocks


def test_demo_runs_as_documented(tmp_path: Path) -> None:
    blocks = _blocks("bash")
    assert blocks, "no runnable bash blocks found on the mailroom page"
    script = "set -euo pipefail\n" + "\n".join(blocks)
    # The venv's bin dir first, so `quints-mailroom` is the workspace build.
    env = dict(os.environ)
    env["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{env.get('PATH', '')}"
    bash = shutil.which("bash") or "/bin/bash"
    proc = subprocess.run(  # noqa: S603 — the script is our own docs page
        [bash, "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"documented demo failed:\n{proc.stdout}\n{proc.stderr}"

    demo = tmp_path / "demo"
    assert (demo / "books" / "inbox" / "acme.pdf").read_bytes().startswith(b"%PDF")
    replies = list(mailbox.Maildir(str(demo / "mail-out"), factory=None))
    assert len(replies) == 1
    reply = replies[0]
    assert reply["To"] == "you@example.com"
    assert reply["Subject"] == "Re: Invoice ACME July"
    assert reply["In-Reply-To"] == "<demo-1@example.com>"


def test_config_examples_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blocks = _blocks("toml")
    assert len(blocks) >= 3, "expected the mailbox config examples on the page"
    # The examples use ~/bookkeeping — resolve ~ into the scratch dir.
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "bookkeeping").mkdir()
    for i, block in enumerate(blocks):
        cfg_dir = tmp_path / f"example-{i}"
        cfg_dir.mkdir()
        path = cfg_dir / "mailroom.toml"
        path.write_text(block + "\n")
        cfg = config_mod.load(path)
        assert cfg.allowed_senders, f"toml example {i} loaded without allowed_senders"
