"""Where a problem in the books is: paths relative to the repository root.

A CI runner points an annotation at a path relative to its checkout, so a
diagnostic's file is spelled that way wherever it is shown: in a report's
table, its ``--json`` and every ``quints check`` format.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Sequence
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Protocol

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


# ── output formats ───────────────────────────────────────────────────────────


class OutputFormat(StrEnum):
    """What ``quints check`` speaks: a person, a script, or a CI runner."""

    text = "text"
    json = "json"
    github = "github"
    gitlab = "gitlab"
    azure = "azure"
    sarif = "sarif"


DOCS = "https://sealambda.github.io/quints/reference/diagnostics/"


class Diagnostic(Protocol):
    """:class:`quints.ledger.Violation`, by shape (``ledger`` imports this module)."""

    @property
    def code(self) -> str: ...
    @property
    def severity(self) -> str: ...
    @property
    def file(self) -> str | None: ...
    @property
    def line(self) -> int | None: ...
    @property
    def date(self) -> str: ...
    @property
    def payee(self) -> str: ...
    @property
    def narration(self) -> str: ...
    @property
    def message(self) -> str: ...
    @property
    def expected(self) -> Decimal: ...
    @property
    def posted(self) -> Decimal: ...


def as_dict(d: Diagnostic) -> dict[str, object]:
    """The ``--json`` shape of one diagnostic."""
    return {
        "code": d.code,
        "severity": d.severity,
        "file": d.file,
        "line": d.line,
        "date": d.date,
        "payee": d.payee,
        "narration": d.narration,
        "message": d.message,
        "expected": str(d.expected),
        "posted": str(d.posted),
    }


class Located(Protocol):
    @property
    def file(self) -> str | None: ...
    @property
    def line(self) -> int | None: ...


def where(d: Located) -> str:
    """``path:line``, ``path``, or "" for a problem with no place in a file."""
    if d.file is None:
        return ""
    return f"{d.file}:{d.line}" if d.line else d.file


def text_line(d: Diagnostic) -> str:
    """``path:line: [warning: ]message [code]``: what editors and ``-efm="%f:%l: %m"`` parse."""
    kind = "warning: " if d.severity == "warning" else ""
    return f"{where(d) or 'quints'}: {kind}{d.message} [{d.code}]"


def counts(ds: Sequence[Diagnostic]) -> str:
    errors = sum(1 for d in ds if d.severity == "error")
    return f"{errors} error(s), {len(ds) - errors} warning(s)"


def _fingerprint(d: Diagnostic, path: str) -> str:
    """Stable across edits that move the entry: the line is left out."""
    parts = (d.code, path, d.date, d.payee, d.narration, d.message)
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


def _escape(value: str, extra: dict[str, str], percent: str = "%25") -> str:
    out = value.replace("%", percent).replace("\r", "%0D").replace("\n", "%0A")
    for char, code in extra.items():
        out = out.replace(char, code)
    return out


def github(ds: Sequence[Diagnostic], summary: str) -> str:
    """GitHub Actions workflow commands, one per diagnostic, then ``summary``.

    Data escapes ``%``, CR and LF; property values also ``:`` and ``,``.
    """
    lines: list[str] = []
    for d in ds:
        props = {"file": d.file, "line": str(d.line) if d.file and d.line else None}
        props["title"] = f"quints {d.code}"
        spec = ",".join(
            f"{k}={_escape(v, {':': '%3A', ',': '%2C'})}" for k, v in props.items() if v
        )
        lines.append(f"::{d.severity} {spec}::{_escape(d.message, {})}")
    return "\n".join([*lines, summary]) + "\n"


def github_summary(ds: Sequence[Diagnostic], title: str) -> str:
    """Every diagnostic as a Markdown table: a step shows only ~10 annotations of each kind."""

    def cell(text: str) -> str:
        return text.replace("|", "\\|").replace("\n", " ")

    rows = [f"| {d.severity} | {where(d)} | `{d.code}` | {cell(d.message)} |" for d in ds]
    head = ["", f"### {title}: {counts(ds)}", "", "| Severity | Location | Code | Message |"]
    return "\n".join([*head, "| --- | --- | --- | --- |", *rows]) + "\n"


def gitlab(ds: Sequence[Diagnostic], default_path: str) -> str:
    """GitLab Code Quality JSON; ``default_path`` places a diagnostic with no file."""
    issues = [
        {
            "description": d.message,
            "check_name": d.code,
            "fingerprint": _fingerprint(d, d.file or default_path),
            "severity": "major" if d.severity == "error" else "minor",
            "location": {"path": d.file or default_path, "lines": {"begin": d.line or 1}},
        }
        for d in ds
    ]
    return json.dumps(issues, indent=2) + "\n"


def azure(ds: Sequence[Diagnostic]) -> str:
    """Azure Pipelines ``task.logissue`` commands.

    ``%`` is ``%AZP25``; property values also escape ``;`` and ``]``.
    """
    lines: list[str] = []
    for d in ds:
        props = {"type": d.severity, "sourcepath": d.file, "linenumber": None, "code": d.code}
        if d.file and d.line:
            props["linenumber"] = str(d.line)
        spec = ";".join(
            f"{k}={_escape(v, {';': '%3B', ']': '%5D'}, '%AZP25')}" for k, v in props.items() if v
        )
        lines.append(f"##vso[task.logissue {spec}]{_escape(d.message, {}, '%AZP25')}")
    return "".join(f"{line}\n" for line in lines)


def sarif(ds: Sequence[Diagnostic], version: str) -> str:
    """SARIF 2.1.0, one run: what reviewdog, Jenkins warnings-ng and Azure read."""
    results: list[dict[str, object]] = []
    for d in ds:
        result: dict[str, object] = {
            "ruleId": d.code,
            "level": d.severity,
            "message": {"text": d.message},
            "partialFingerprints": {"quints/v1": _fingerprint(d, d.file or "")},
        }
        if d.file is not None:
            physical: dict[str, object] = {"artifactLocation": {"uri": d.file}}
            if d.line:
                physical["region"] = {"startLine": d.line}
            result["locations"] = [{"physicalLocation": physical}]
        results.append(result)
    rules = [{"id": code, "helpUri": f"{DOCS}#{code}"} for code in sorted({d.code for d in ds})]
    driver = {"name": "quints", "version": version, "informationUri": DOCS, "rules": rules}
    log = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{"tool": {"driver": driver}, "results": results}],
    }
    return json.dumps(log, indent=2) + "\n"
