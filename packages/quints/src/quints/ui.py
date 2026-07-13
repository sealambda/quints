"""Shared terminal presentation — a single Rich console + formatting helpers.

Rendering lives here (and in per-command ``render`` functions) so that command
logic stays presentation-free: it returns data structures, this layer turns them
into output. A future non-terminal front-end (web, Textual TUI, agent) can reuse
the same data and swap only this layer.
"""

from __future__ import annotations

from decimal import Decimal

from rich.console import Console
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
