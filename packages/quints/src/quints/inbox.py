"""Inbox inventory: documents waiting to be booked (docs/plans/06 step 2).

Deterministic only — no AI here. For each file in ``inbox/`` this reports
what the filename convention already tells us (date, payee, narrative),
whether an identical file is already filed under ``documents/`` (content
hash), and whether the basename is already linked from a transaction's
``document:`` metadata. The judgment layer (Claude) consumes this JSON to
decide what each document *is*; the engine only says what's known.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from beancount.core import data
from rich import box
from rich.console import Console
from rich.table import Table

from . import config, ledger, ui

_NAMED = re.compile(r"^(\d{4}-\d{2}-\d{2})\.([^.]+)\.(.+)\.(\w+)$")


@dataclass
class InboxDoc:
    name: str
    size: int
    sha256: str                     # short content hash (12 hex chars)
    date_hint: str | None           # from the YYYY-MM-DD.payee.narrative.ext convention
    payee_hint: str | None
    narrative_hint: str | None
    duplicate_of: str | None        # identical file already under documents/
    linked: bool                    # basename referenced by a document: metadata


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def _linked_basenames(entries) -> set[str]:
    names: set[str] = set()
    for e in entries:
        if not isinstance(e, data.Transaction) or not e.meta:
            continue
        for key, value in e.meta.items():
            if key.startswith("document"):
                names.add(str(value))
    return names


def scan(root: Path, entries) -> list[InboxDoc]:
    box_dir = root / "inbox"
    if not box_dir.is_dir():
        return []
    files = sorted(p for p in box_dir.iterdir()
                   if p.is_file() and not p.name.startswith(".")
                   and not p.name.lower().startswith("readme"))
    if not files:
        return []

    # Content-hash dedup against documents/, prefiltered by size so we only
    # hash filed documents that could possibly collide.
    inbox_sizes = {p.stat().st_size for p in files}
    filed_by_hash: dict[str, Path] = {}
    docs_dir = root / "documents"
    if docs_dir.is_dir():
        for p in docs_dir.rglob("*"):
            if p.is_file() and p.stat().st_size in inbox_sizes:
                filed_by_hash.setdefault(_sha(p), p)

    linked = _linked_basenames(entries)
    out = []
    for p in files:
        m = _NAMED.match(p.name)
        dup = filed_by_hash.get(_sha(p))
        out.append(InboxDoc(
            name=p.name,
            size=p.stat().st_size,
            sha256=_sha(p),
            date_hint=m.group(1) if m else None,
            payee_hint=m.group(2) if m else None,
            narrative_hint=m.group(3) if m else None,
            duplicate_of=str(dup.relative_to(root)) if dup else None,
            linked=p.name in linked,
        ))
    return out


def compute(ledger_path: Path, cfg: config.Config | None = None) -> list[InboxDoc]:
    entries, _ = ledger.load_entries(ledger_path)
    return scan(ledger_path.resolve().parent, entries)


# ── render ────────────────────────────────────────────────────────────────────

def render(docs: list[InboxDoc], console: Console | None = None) -> None:
    console = console or ui.console
    console.print()
    console.rule("[bold]Inbox[/]")
    if not docs:
        console.print("[ok]Inbox is empty.[/]")
        console.print()
        return
    t = Table(box=box.SIMPLE_HEAVY, pad_edge=False)
    t.add_column("Document")
    t.add_column("Date", no_wrap=True)
    t.add_column("Payee")
    t.add_column("Status")
    for d in docs:
        if d.duplicate_of:
            status = f"[warn]duplicate of {d.duplicate_of}[/]"
        elif d.linked:
            status = "[warn]already linked in ledger[/]"
        else:
            status = "[muted]to book[/]"
        t.add_row(d.name, d.date_hint or "—", d.payee_hint or "—", status)
    console.print(t)
    console.print()
