"""Where a problem in the books is: paths relative to the repository root.

A CI runner points an annotation at a path relative to its checkout, so a
diagnostic's file is spelled that way wherever it is shown: in a report's
table, its ``--json`` and every ``quints check`` format.
"""

from __future__ import annotations

import os
from pathlib import Path

# The checkout each CI runner names: GitHub Actions, GitLab CI, Azure Pipelines.
ROOT_VARIABLES = ("GITHUB_WORKSPACE", "CI_PROJECT_DIR", "BUILD_SOURCESDIRECTORY")


def repo_root(start: Path) -> Path:
    """The runner's checkout, else the git work tree holding ``start``, else the CWD.

    The work tree is the nearest directory with a ``.git`` (a directory, or
    the file a worktree or submodule has): what ``git rev-parse
    --show-toplevel`` answers, without a subprocess.
    """
    for variable in ROOT_VARIABLES:
        if value := os.environ.get(variable):
            return Path(value)
    for directory in (start, *start.parents):
        if (directory / ".git").exists():
            return directory
    return Path.cwd()


def relative(path: str) -> str:
    """``path`` relative to :func:`repo_root`, as given when it lies outside it."""
    file = Path(path).resolve()  # beancount keeps symlinks; git and runners may not
    root = repo_root(file.parent).resolve()
    return file.relative_to(root).as_posix() if file.is_relative_to(root) else path
