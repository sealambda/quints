"""What a change does to the books, in money: `quints diff`.

A diff of the ``.bean`` files shows text. This loads the ledger twice, the
base and the head, and lists the transactions added or removed and how the
statutory figures move. It is a review aid, not a gate, and a base that
doesn't load is treated as empty: the change may be the fix.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tarfile
import tempfile
from collections import Counter
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date as Date
from decimal import Decimal
from pathlib import Path

from beancount.core import data

from . import config, diagnostics, kmu, ledger, mwst

GIT_TIMEOUT_SECONDS = 60
MAX_LISTED = 25  # per list, in text and Markdown; the JSON has them all


@dataclass
class Booking:
    """One transaction added or removed."""

    date: str
    payee: str
    narration: str
    amounts: str  # the debits, e.g. "1,081.00 CHF"
    documented: bool
    file: str | None
    line: int | None


@dataclass
class Figure:
    label: str
    base: Decimal
    head: Decimal
    change: Decimal


@dataclass
class Diff:
    base: str
    base_loaded: bool  # False: the base was missing and counted as empty
    transactions: int  # in the head
    added: list[Booking] = field(default_factory=list)
    removed: list[Booking] = field(default_factory=list)
    figures: list[Figure] = field(default_factory=list)  # only those that moved


_Key = tuple[Date, str, str, tuple[tuple[str, str, str], ...]]


def _keyed(entries: data.Directives) -> dict[tuple[_Key, int], data.Transaction]:
    """Transactions keyed by content, not position. Identical bookings get an
    occurrence counter, so a second copy shows as one addition."""
    seen: Counter[_Key] = Counter()
    out: dict[tuple[_Key, int], data.Transaction] = {}
    for e in entries:
        if not isinstance(e, data.Transaction):
            continue
        legs = tuple(
            sorted(
                (p.account, str(p.units.number), p.units.currency)
                if p.units
                else (p.account, "", "")
                for p in e.postings
            )
        )
        key = (e.date, e.payee or "", e.narration or "", legs)
        seen[key] += 1
        out[(key, seen[key])] = e
    return out


def _booking(e: data.Transaction) -> Booking:
    debits = [
        f"{p.units.number:,.2f} {p.units.currency}"
        for p in e.postings
        if p.units is not None and p.units.number is not None and p.units.number > 0
    ]
    file, line = ledger.location(e.meta)
    return Booking(
        str(e.date),
        e.payee or "",
        e.narration or "",
        ", ".join(debits),
        ledger.has_document(e),
        file,
        line,
    )


def _period_at(at: Date, cfg: config.Config) -> str | None:
    """The VAT period holding ``at`` (2026-Q3, 2026-H2, 2026), None when not liable."""
    phase = cfg.phase_at(at)
    if phase is None:
        return None
    if phase.period == "quarter":
        return f"{at.year}-Q{(at.month - 1) // 3 + 1}"
    if phase.period == "half-year":
        return f"{at.year}-H{1 if at.month <= 6 else 2}"
    return str(at.year)


def _figures(path: Path, loaded: ledger.Loaded, at: Date, cfg: config.Config) -> dict[str, Decimal]:
    year = at.year
    erfolg = kmu.compute_erfolg(path, f"{year}-01-01", f"{year}-12-31", cfg, loaded=loaded)
    bilanz = kmu.compute_bilanz(path, str(at), cfg, loaded=loaded)
    out = {
        f"Result {year}": erfolg.result,
        f"EBIT {year}": erfolg.ebit,
        f"Total assets {at}": bilanz.total_assets,
    }
    if period := _period_at(at, cfg):
        vat = mwst.compute(path, *mwst.period_range(period), cfg, loaded=loaded)
        out[f"VAT payable {period} (Ziffer 500)"] = vat.z500
        out[f"Input VAT {period} (Ziffer 400)"] = vat.z400
    return out


def compute(head: Path, base: Path, at: Date, cfg: config.Config | None = None) -> Diff:
    """``head`` against ``base``; a missing ``base`` counts as empty books."""
    cfg = cfg or config.get()
    head_loaded = ledger.load(head)
    base_loaded: ledger.Loaded = ([], [])
    if base.exists():
        base_loaded = ledger.load(base)
    head_txns, base_txns = _keyed(head_loaded[0]), _keyed(base_loaded[0])
    before = _figures(base, base_loaded, at, cfg)
    after = _figures(head, head_loaded, at, cfg)
    return Diff(
        base=str(base),
        base_loaded=base.exists(),
        transactions=len(head_txns),
        added=[_booking(head_txns[k]) for k in sorted(head_txns.keys() - base_txns.keys())],
        removed=[_booking(base_txns[k]) for k in sorted(base_txns.keys() - head_txns.keys())],
        figures=[
            Figure(k, before[k], after[k], after[k] - before[k])
            for k in after
            if before[k] != after[k]
        ],
    )


@contextmanager
def at_ref(ledger_path: Path, ref: str) -> Generator[Path]:
    """The ledger as it is at git ``ref``, from ``git archive`` in a temporary
    directory: no worktree, nothing written to the repository.

    shortcut: archives the whole tree, documents included; restrict it to the
    ledger's files if a large repository makes this slow.
    """
    git = shutil.which("git")
    if git is None:
        raise ValueError("--base-ref needs git, and git is not installed")
    absolute = Path(os.path.abspath(ledger_path))

    def run(where: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(  # noqa: S603 — fixed argv, git resolved via shutil.which
            [git, "-C", str(where), *args],
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )

    top = run(absolute.parent, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        raise ValueError(f"--base-ref: {absolute.parent} is not in a git repository")
    root = Path(top.stdout.decode().strip())
    archive = run(root, "archive", "--format=tar", ref)
    if archive.returncode != 0:
        raise ValueError(
            f"--base-ref: git cannot read {ref!r} ({archive.stderr.decode().strip()}). "
            "A shallow CI checkout lacks the base: fetch it (actions/checkout: fetch-depth: 0)"
        )
    with tempfile.TemporaryDirectory(prefix="quints-base-") as tmp:
        with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
            tar.extractall(tmp, filter="data")
        yield Path(tmp) / absolute.resolve().relative_to(root.resolve())


def render(diff: Diff, markdown: bool) -> str:
    """The diff as text, or as a ready-made pull-request comment."""
    lines: list[str] = ["## Ledger impact" if markdown else "Ledger impact", ""]
    if not diff.base_loaded:
        lines += [f"The base ({diff.base}) did not load: compared against empty books.", ""]
    if not diff.added and not diff.removed:
        lines += ["No transactions added or removed.", ""]
    else:
        bold = "**" if markdown else ""
        lines += [
            f"{bold}{len(diff.added)} added{bold}, {bold}{len(diff.removed)} removed{bold} "
            f"({diff.transactions} transactions in all).",
            "",
        ]
    for title, bookings in (("Added", diff.added), ("Removed or changed", diff.removed)):
        if not bookings:
            continue
        lines += [f"<details open><summary>{title}</summary>", ""] if markdown else [f"{title}:"]
        for b in bookings[:MAX_LISTED]:
            payee = f"{b.payee} — " if b.payee else ""
            marker = "documented" if b.documented else "no document"
            where = diagnostics.where(b)
            place = f" ({where})" if where else ""
            lines.append(f"- {b.date} {payee}{b.narration} · {b.amounts} · {marker}{place}")
        if len(bookings) > MAX_LISTED:
            lines.append(f"- …and {len(bookings) - MAX_LISTED} more")
        lines += ["", "</details>", ""] if markdown else [""]
    if not diff.figures:
        lines += ["No movement in the statutory figures."]
    elif markdown:
        lines += ["| Figure | Base | Head | Change |", "|---|---:|---:|---:|"]
        lines += [
            f"| {f.label} | {f.base:,.2f} | {f.head:,.2f} | {f.change:+,.2f} CHF |"
            for f in diff.figures
        ]
    else:
        lines += [
            f"{f.label}: {f.base:,.2f} → {f.head:,.2f} ({f.change:+,.2f} CHF)" for f in diff.figures
        ]
    return "\n".join(lines).rstrip() + "\n"
