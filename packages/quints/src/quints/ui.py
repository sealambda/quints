"""Shared terminal presentation — a single Rich console + formatting helpers.

Rendering lives here (and in per-command ``render`` functions) so that command
logic stays presentation-free: it returns data structures, this layer turns them
into output. A future non-terminal front-end (web, Textual TUI, agent) can reuse
the same data and swap only this layer.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Protocol

from rich import box
from rich.console import Console
from rich.table import Table
from rich.theme import Theme

THEME = Theme(
    {
        "ziffer": "dim cyan",
        "owe": "bold red",
        "refund": "bold green",
        "ok": "green",
        "warn": "yellow",
        "err": "bold red",
        "muted": "dim",
    }
)

console = Console(theme=THEME)
err_console = Console(stderr=True, theme=THEME)


def money(value: Decimal) -> str:
    """Thousands-separated, 2-decimal amount (no currency symbol)."""
    return f"{value:,.2f}"


class ViolationLike(Protocol):
    """:class:`quints.ledger.Violation`, by shape (``ui`` and ``ledger`` are siblings)."""

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


def violations_table(violations: Sequence[ViolationLike], title: str, lang: str = "en") -> Table:
    """The problems a report found in the books, under its figures.

    The expected/posted columns only appear when some violation carries an
    amount — a loader error has none.
    """
    de = lang == "de"
    t = Table(box=box.SIMPLE, title=title, title_justify="left", title_style="warn")
    t.add_column("Datum" if de else "Date", style="muted", no_wrap=True)
    t.add_column("Payee")
    t.add_column("Narration")
    t.add_column("Problem")
    amounts = any(v.expected or v.posted for v in violations)
    if amounts:
        t.add_column("Erwartet" if de else "Expected", justify="right", no_wrap=True)
        t.add_column("Gebucht" if de else "Posted", justify="right", no_wrap=True)
    for v in violations:
        cells = [v.date, v.payee, v.narration, f"[warn]{v.message}[/]"]
        if amounts:
            cells += [money(v.expected), money(v.posted)]
        t.add_row(*cells)
    return t
