"""Every diagnostic points at a file relative to the repository root.

A CI runner resolves an annotation's path against its checkout, and a person
reads it against the repository, wherever the books sit inside it.
"""

from pathlib import Path

import pytest
from beancount import loader
from rich.console import Console

from quints import diagnostics, ledger, ui

TXN = '2026-01-01 open Assets:Bank CHF\n2026-01-02 * "Acme" "fee"\n  Assets:Bank  -1 CHF\n'


def test_paths_are_relative_to_the_runner_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    books = tmp_path / "books" / "sub"
    books.mkdir(parents=True)
    monkeypatch.setenv("CI_PROJECT_DIR", str(tmp_path))
    assert diagnostics.relative(str(books / "main.bean")) == "books/sub/main.bean"
    outside = tmp_path.parent / "elsewhere.bean"
    assert diagnostics.relative(str(outside)) == str(outside)  # outside: as given
    monkeypatch.setenv("GITHUB_WORKSPACE", str(books))  # GitHub's variable wins
    assert diagnostics.relative(str(books / "main.bean")) == "main.bean"


def test_paths_are_relative_to_the_git_work_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".git").mkdir()
    books = tmp_path / "books"
    books.mkdir()
    monkeypatch.chdir(books)  # not relative to the CWD: to the work tree
    assert diagnostics.relative(str(books / "2026.bean")) == "books/2026.bean"
    link = tmp_path.parent / f"{tmp_path.name}-link"
    link.symlink_to(tmp_path)  # beancount keeps the symlinked spelling
    assert diagnostics.relative(str(link / "books" / "2026.bean")) == "books/2026.bean"


def test_violation_at_takes_the_entry_location(tmp_path: Path) -> None:
    led = tmp_path / "main.bean"
    led.write_text(TXN)
    entries, _, _ = loader.load_file(str(led))
    v = ledger.violation_at(entries[-1], "odd", code="x-test", severity="warning")
    assert (v.date, v.payee, v.narration, v.file, v.line) == (
        "2026-01-02",
        "Acme",
        "fee",
        str(led),
        2,
    )
    assert v.located == f"{led}:2: odd"
    opened = ledger.violation_at(entries[0], "odd", code="x-test")
    assert (opened.date, opened.payee, opened.line) == ("2026-01-01", "", 1)
    unfiled, _, _ = loader.load_string(TXN)  # "<string>": no file to point at
    v = ledger.violation_at(unfiled[-1], "odd", code="x-test")
    assert (v.file, v.line, v.located) == (None, None, "odd")


def test_the_table_shows_the_location_when_there_is_one() -> None:
    def text(violations: list[ledger.Violation]) -> str:
        console = Console(width=200, record=True, theme=ui.THEME)
        console.print(ui.violations_table(violations, "Problems"))
        return console.export_text()

    bare = ledger.Violation("2026-01-02", "", "", "gap", code="x-test")
    assert "Location" not in text([bare])
    located = ledger.Violation("2026-01-02", "", "", "odd", code="x-test", file="b/m.bean", line=3)
    assert "Location" in text([bare, located]) and "b/m.bean:3" in text([bare, located])
